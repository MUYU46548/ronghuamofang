# -*- coding: utf-8 -*-
"""段落精修的**失败分因**与**开跑前预算校验**（P1-A4，2026-11-17）—— 零 LLM、零网络。

## 为什么

`docs/读改链走查-20261117.md` 问题 C：这张卡要求模型**把整章写回**（4000–7200 汉字），
而 `prompts/stage4_refine_paragraph.md` 的 `max_tokens` 只有 4000（≈2400 字），
于是长章要么被截断、要么模型自己删；而回执只说"段落重写子会话失败"——
作者看不出该去调上限、该重试、还是该看 error。

两条判据：
1. **装不下就不发请求**：预算够不上整章时直接拒，且**不产生备份**（不制造噪音文件）；
2. **失败要说清是哪一种**：截断 / 超时 / 其它（带引擎的 error 尾巴）。

用法：python tests/unit/test_paragraph_refine_failures.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import refine_paragraph as rp     # noqa: E402

PASS, FAIL = [], []

PROMPT_REL = Path("prompts") / "stage4_refine_paragraph.md"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


class FakeClient(object):
    """够用的引擎替身：记调用次数，可按剧本改盘、按剧本返回退出码。"""

    def __init__(self, result=None, mutate=None):
        self.result = result or {"exit_code": 0, "stdout_tail": ""}
        self.mutate = mutate
        self.calls = 0
        self.task = None

    def write_task(self, task_dir, name, content):
        p = Path(task_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self.task = p
        return p

    def run_task(self, task, session_id=None):
        self.calls += 1
        if self.mutate:
            self.mutate()
        return self.result


def build_root(max_tokens, words=600):
    """临时项目根：一章正文 + 一张可调 max_tokens 的精修卡。"""
    tmp = Path(tempfile.mkdtemp(prefix="parafail_"))
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    body = "他把杯子放下，看着窗外的雨。\n\n" + ("雨声连成一片，屋里很静。" * (words // 10))
    (tmp / "data" / "chapters" / "raw" / "03.md").write_text(
        "## 第3章 雨夜\n\n" + body, encoding="utf-8")
    p = tmp / PROMPT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nstage: 4\nname: stage4_refine_paragraph\n"
                 "max_tokens: %d\ntemperature: 0.3\n---\n正文\n" % max_tokens,
                 encoding="utf-8")
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
    return tmp


def run(tmp, client, chapter_words_from=None):
    cfg = {"budget": {"token_limit": {"enabled": False}}}
    return rp.run_paragraph_refine(cfg, {}, 3, 0, "改得别那么口语", client=client,
                                   task_dir=str(tmp / "data" / "state" / "tasks"),
                                   preview=True, root=str(tmp))


def main():
    print("=" * 62)
    print("  段落精修：预算前置校验 + 失败分因")
    print("=" * 62)

    print("\n[1] 装不下就不发请求（max_tokens 只够 2400 字，本章 5000+ 字）")
    tmp = build_root(4000, words=5000)
    client = FakeClient()
    ok, msg, backup, _v, _info = run(tmp, client)
    check("1 拒绝执行", ok is False, ok)
    check("1 说清是预算装不下", "预算装不下" in msg, msg)
    check("1 文案点明解决办法（调大 max_tokens）", "max_tokens" in msg, msg)
    check("1 **一个模型调用都没发**", client.calls == 0, client.calls)
    check("1 没产生备份文件（不留噪音）",
          not list((tmp / "data" / "chapters" / "history").glob("ch*.md"))
          if (tmp / "data" / "chapters" / "history").exists() else True)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[2] 装得下 → 真的发调用；截断要说成截断")
    tmp = build_root(40000)
    client = FakeClient({"exit_code": 2, "truncated": True,
                         "error": "输出被 max_tokens 截断（finish_reason=length）"})
    ok, msg, backup, _v, _info = run(tmp, client)
    check("2 发了调用", client.calls == 1, client.calls)
    check("2 失败", ok is False, ok)
    check("2 文案说截断", "截断" in msg, msg)
    check("2 文案给了处置（调大 max_tokens）", "max_tokens" in msg, msg)
    check("2 失败也留了备份（可回退）", backup is not None, backup)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[3] 超时 ≠ 截断")
    tmp = build_root(40000)
    ok, msg, _b, _v, _i = run(tmp, FakeClient({"exit_code": 124, "error": "timeout after 600s"}))
    check("3 文案说超时", "超时" in msg, msg)
    check("3 不再用“子会话失败”糊过去", "子会话失败" not in msg, msg)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[4] 其它失败带上引擎的 error（作者能去查）")
    tmp = build_root(40000)
    ok, msg, _b, _v, _i = run(tmp, FakeClient({"exit_code": 1, "error": "provider 400: bad request"}))
    check("4 带 exit 码", "exit=1" in msg, msg)
    check("4 带 error 原文", "provider 400" in msg, msg)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[5] 段落数变化过大仍按老口径拒（回归）")
    tmp = build_root(40000)

    def blow_up():
        p = tmp / "data" / "chapters" / "raw" / "03.md"
        p.write_text("## 第3章雨夜\n\n一段。\n\n两段。\n\n三段。\n\n四段。\n\n五段。\n\n"
                     "六段。\n\n七段。\n\n八段。\n", encoding="utf-8")

    ok, msg, _b, _v, _i = run(tmp, FakeClient(mutate=blow_up))
    check("5 判失败", ok is False, ok)
    check("5 说清是段落数变化", "段落数变化" in msg, msg)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[6] 正常路径：只算不落（正文一个字不动）")
    tmp = build_root(40000)
    before = (tmp / "data" / "chapters" / "raw" / "03.md").read_text(encoding="utf-8")
    ok, msg, _b, _v, info = run(tmp, FakeClient())
    after = (tmp / "data" / "chapters" / "raw" / "03.md").read_text(encoding="utf-8")
    check("6 成功", ok is True, msg)
    check("6 正文没被改（preview 回滚）", before == after)
    check("6 交出了候选", bool((info or {}).get("candidates")), info)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[7] 设置里的单请求上限也参与取小（cap=3000 时 40000 的卡照样拒）")
    tmp = build_root(40000, words=5000)
    cfg = {"budget": {"token_limit": {"enabled": True, "per_request_max_tokens": 3000}}}
    ok, msg, _b, _v, _i = rp.run_paragraph_refine(
        cfg, {}, 3, 0, "改", client=FakeClient(),
        task_dir=str(tmp / "data" / "state" / "tasks"), preview=True, root=str(tmp))
    check("7 按小值拒", ok is False and "预算装不下" in msg, msg)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
