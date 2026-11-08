# -*- coding: utf-8 -*-
"""运行中插一句（2026-11-02，不可用清单第 4 条）。

## 要治的病

用户原话的意思是：**运行中改不了方向。** 想调整只能等它跑完，然后重写已写的章。
`/intervene` 此前全项目 0 命中。

## 判据（每条都能变红）

- I1 空指令拒收（不写一个空文件让下游猜）
- I2 写→读 往返一致
- I3 **用掉即删**（一次性；留着的指令会变成第二条大纲）
- I4 只对目标章生效：`target_chapter` 为空 = 下一章；给了号 = 只有那章
- I5 任务里的那段必须说清三件事：作者当场要求 / 只对本章 / locked 优先
- I6 stage4 真的接了（不是只写了模块）

用法：python tests/unit/test_intervene.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import intervene                                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("运行中插一句")
    print("=" * 66)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        p = intervene.path(root)
        check("I0 文件路径在 data/state 下", "state" in str(p), p)

        # ---- I1 空 ----
        for bad in ("", "   ", None):
            try:
                intervene.write(bad, root=root)
                check("I1 空指令 %r 必须拒收" % (bad,), False, "被接受了")
            except ValueError:
                check("I1 空指令 %r 拒收" % (bad,), True)

        # ---- I2 往返 ----
        data = intervene.write("这一章别写打斗，写两个人吃饭时的沉默", root=root)
        back = intervene.read(root=root)
        check("I2 写→读一致", back and back["instruction"] == data["instruction"], back)
        check("I2 带时间戳", bool(back and back.get("at")), back)

        # ---- I4 适用章 ----
        check("I4 没指定章 → 对任何章都算待用", intervene.applies_to(back, 1) is True)
        check("I4 没指定章 → 第 9 章也算", intervene.applies_to(back, 9) is True)
        d7 = intervene.write("第七章要收尾", target_chapter=7, root=root)
        check("I4 指定第 7 章 → 第 7 章适用", intervene.applies_to(d7, 7) is True)
        check("I4 指定第 7 章 → 第 3 章不适用", intervene.applies_to(d7, 3) is False)
        check("I4 空数据不适用", intervene.applies_to(None, 1) is False)

        # ---- I3 用掉即删 ----
        got = intervene.consume(root=root)
        check("I3 consume 拿到内容", got and "第七章" in got["instruction"], got)
        check("I3 consume 之后文件没了", not p.exists())
        check("I3 再读就是 None", intervene.read(root=root) is None)

        # ---- I7 裁定账本（2026-11-08，竞品三路之三）：用完即删也要留痕 ----
        hist = intervene.history(root=root)
        acts = [h.get("action") for h in hist]
        check("I7 登记与取走都进流水", "set" in acts and "used" in acts, acts)
        check("I7 流水里能看见插过什么",
              any("第七章要收尾" in str(h.get("instruction")) for h in hist), hist[:3])
        check("I7 流水是倒序（最近的在最前）", acts and acts[0] == "used", acts)
        check("I7 再写一条也进流水",
              intervene.write("再插一句", root=root) and
              intervene.history(root=root)[0].get("action") == "set",
              intervene.history(root=root)[:2])

        # ---- I5 渲染 ----
        block = intervene.render_block({"instruction": "少写景，多写对话\n结尾留白"})
        check("I5 标明作者当场要求", "作者当场要求" in block)
        check("I5 说是只对本章", "本章" in block)
        check("I5 locked 优先写清楚", "locked" in block and "为准" in block)
        check("I5 多行原文被引起来", "> 少写景，多写对话" in block and "> 结尾留白" in block,
              block[:120])

    # ---- I6 接线 ----
    src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("I6 stage4 接了取指令函数", "_take_intervention(n)" in src)
    check("I6 注入发生在写任务之前",
          src.find("_task_text += intervene.render_block") <
          src.find('client.write_task(task_dir, f"stage4_ch{n:02d}.md", _task_text)'),
          "注入应发生在 write_task 之前")
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("I6 API 有登记端点", 'p == "/intervene"' in api_src)
    check("I6 API 能读裁定账本", 'p == "/intervene/history"' in api_src)
    check("I6 没在跑时拒收", "现在没有流水线在跑" in api_src)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
