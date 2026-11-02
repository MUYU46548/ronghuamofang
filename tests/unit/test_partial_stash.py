# -*- coding: utf-8 -*-
"""停止时的残稿落地（2026-11-02，不可用清单第 3 条）。

## 要治的病

停止 = 当前章半截**直接丢弃**，一个字都不留。用户于是只能"丢进去一直跑"——
停一次的定价是一整章，理性选择就是不停。

## 判据（每条都能变红）

- S1 有流式缓冲 → 落成 `truncated/chNN_停在_*.txt`，字数与内容对得上
- S2 agent 引擎（缓冲空、但正文文件已存在）→ 直接指向该文件，不复制第二份
- S3 什么都没收到 → `(None, 0)`，不凭空造一个空残稿
- S4 落盘失败 → 不抛（存残稿失败不能把"停止"本身变成错误）
- S5 落盘文件名带章号与时间，能在残稿列表里认出来

用法：python tests/unit/test_partial_stash.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import truncation                                       # noqa: E402
from stage4_writing import _stash_partial                          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("停止时的残稿落地")
    print("=" * 66)
    old_dir = truncation.TRUNC_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            truncation.TRUNC_DIR = t / "truncated"

            # ---- S1 有缓冲 ----
            buf = ["第3章的", "开头两千字", "，写到一半被停。"]
            path, chars = _stash_partial(3, t / "raw" / "03.md", buf)
            p = Path(path) if path else None
            check("S1 落成一个文件", bool(p and p.exists()), path)
            check("S1 字数对得上", chars == len("".join(buf)), (chars, len("".join(buf))))
            check("S1 内容一字不差", bool(p) and p.read_text(encoding="utf-8") == "".join(buf),
                  p and p.read_text(encoding="utf-8")[:40])
            check("S5 文件名带章号", bool(p) and "ch03" in p.name, p and p.name)
            check("S5 文件落在残稿目录", bool(p) and p.parent == truncation.TRUNC_DIR, p and p.parent)

            # ---- S2 agent 引擎：正文已在文件里 ----
            raw = t / "raw" / "05.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("模型用 write_file 写了半章", encoding="utf-8")
            path2, chars2 = _stash_partial(5, raw, [])
            check("S2 指向已有正文文件", path2 == str(raw), path2)
            check("S2 不复制第二份", len(list(truncation.TRUNC_DIR.glob("ch05*"))) == 0)
            check("S2 字数按文件算", chars2 == len("模型用 write_file 写了半章"), chars2)

            # ---- S3 什么都没有 ----
            path3, chars3 = _stash_partial(7, t / "raw" / "07.md", [])
            check("S3 空 → 不造空残稿", (path3, chars3) == (None, 0), (path3, chars3))

            # ---- S4 落盘失败不抛 ----
            truncation.TRUNC_DIR = Path("\0坏路径") if False else (t / "blocked")
            (t / "blocked").write_text("我是个文件不是目录", encoding="utf-8")
            try:
                r = _stash_partial(9, t / "raw" / "09.md", ["有内容"])
                check("S4 落盘失败只告警、不抛", r == (None, 0), r)
            except Exception as e:                             # noqa: BLE001
                check("S4 落盘失败只告警、不抛", False, repr(e))
    finally:
        truncation.TRUNC_DIR = old_dir

    # ---- S6/S7 接线（防"函数写了但没人调"的假绿）----
    # 只测函数本身会漏掉最常见的一种失效：函数好好的，停止分支里没接上。
    # 所以用 AST 看真实源码的 if 分支。
    import ast
    src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    wired = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            try:
                cond = ast.unparse(node.test)
            except Exception:                              # noqa: BLE001
                cond = ""
            # ast.unparse 会把双引号改成单引号，别按字面比字符串
            if "result.get(" in cond and "stopped" in cond:
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call)
                            and getattr(sub.func, "id", "") == "_stash_partial"):
                        wired = True
    check("S6 停止分支真的调了落盘（不是只写了函数）", wired,
          "停止分支应调用 _stash_partial")

    buffered = '_ch_state["buf"].append(piece)' in src
    check("S7 流式回调真的在攒正文", buffered, "流式回调应有 _ch_state[\"buf\"].append")
    check("S7 换章会清空缓冲", '_ch_state["buf"] = []' in src)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
