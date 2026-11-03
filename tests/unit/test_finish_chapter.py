# -*- coding: utf-8 -*-
"""写完这章就停（2026-11-02，一期 F04 真暂停的后半）。

## 要治的病

界面上只有“停止”：它**立刻**掐断正在生成的那一章。想"把手上的活干完再停"
没有对应的动作 —— 于是要么硬停（半截存残稿），要么不敢停。

## 它与“停止”是两件事，判据必须证明这一点

- 停止：马上掐断，半截进 `truncated/`
- 本功能：本章写完 → 入稿 → 标完成 → 才停；那一章是**完整的**

## 判据（每条都能变红）

- P1 默认没有标志（不能凭空"已暂停"）
- P2 登记 / 消费 / 取消三条路
- P3 章末检查在 `_finalize` **之后**（先落好状态再停，断了不丢东西）
- P4 orchestrator 的拦截在**自动重试之前**（否则"暂停"会被重试立刻续上）
- P5 暂停不标 failed（状态回 pending）—— 与中断同口径
- P6 端点存在，且**不被施工权拦**（它存在的意义就是 AI 持锁时人也能停）
- P7 下一轮开始会清掉上一轮的 paused_at（否则一运行就被当成"已暂停"）

用法：python tests/unit/test_finish_chapter.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import pause_flag                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("写完这章就停")
    print("=" * 66)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # ---- P1 默认没有 ----
        check("P1 新项目里没有标志", pause_flag.is_requested(root=root) is False)
        check("P1 没登记时 consume 返回 None", pause_flag.consume(root=root) is None)

        # ---- P2 登记 / 消费 / 取消 ----
        d = pause_flag.request(root=root, note="gui")
        check("P2 登记后能查到", pause_flag.is_requested(root=root) is True)
        check("P2 登记带时间戳", bool(d.get("at")), d)
        got = pause_flag.consume(root=root)
        check("P2 consume 拿到内容", got and got.get("note") == "gui", got)
        check("P2 consume 后清掉（只停一次）", pause_flag.is_requested(root=root) is False)

        pause_flag.request(root=root)
        pause_flag.clear(root=root)
        check("P2 clear 也能清", pause_flag.is_requested(root=root) is False)

    # ---- P3~P7 接线 ----
    s4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    i_finalize = s4.find('if _finalize(n, chap_path, result, check) == "budget":')
    i_pause = s4.find("pause_flag.is_requested()")
    check("P3 章末检查在 _finalize 之后（先落好状态再停）",
          0 < i_finalize < i_pause, (i_finalize, i_pause))
    check("P5 暂停不标 failed（回 pending + paused_at）",
          'set_stage(4, "pending",\n                               paused_at=' in s4
          or 'paused_at=time.strftime' in s4, "应有 set_stage(4, \"pending\", paused_at=…)")
    check("P7 下一轮开始清 paused_at",
          '.pop("paused_at", None)' in s4, "应有 pop(\"paused_at\", None)")

    orc = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    i_guard = orc.find('_st_n.get("paused_at")')
    i_retry = orc.find("自动重试（默认开）")
    check("P4 暂停拦截在自动重试之前", 0 < i_guard < i_retry, (i_guard, i_retry))
    check("P4 拦截后写 paused 状态", '_finalize("paused", 4)' in orc)

    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("P6 登记端点存在", '"/pipeline/finish_chapter"' in api)
    guard = (ROOT / "scripts" / "utils" / "lock_guard.py").read_text(encoding="utf-8")
    check("P6 不被施工权拦（AI 持锁时人也能停）",
          '"/pipeline/finish_chapter"' in guard)
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("P6 界面有“写完这章就停”", "写完这章就停" in app and "toggleFinishChapter" in app)
    check("P6 停止的提示语已更新（半截存残稿，不再是“不入稿”）",
          "半截会存成残稿" in app, "应是“半截会存成残稿”")

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
