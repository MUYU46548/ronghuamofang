# -*- coding: utf-8 -*-
"""`dispatch_run` 的判据：段号解析必须严，产出必须落候选、绝不碰正文。

## 为什么这些判据值得写

派单是**一次调用改多段**，一个批里 N 段的产出靠模型输出的段号归位。
段号错位的后果不是报错，而是 **A 段的改写被写进 B 段** —— 正文被毁，
作者还很难看出来（两边都是通顺的中文）。所以本用例的重点全在"错位"上：

1. 变形段号要认（`===SEGMENT: #7===` / 全角冒号 / 前后空格）；
2. **越界段号一律丢弃**，绝不塞给邻近段；
3. **缺失的段不补、不猜**，如实报 missing；
4. 截断（finish_reason=length）整批作废，一条候选都不许进库；
5. 任何失败路径都**不碰正文**（正文 sha256 前后一致）。

用法：python tests/unit/test_dispatch_run.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + ("  → " + str(detail)[:300] if detail else ""))


class FakeClient:
    """按预设回复的作者假客户端；不联网、不花 token。"""

    def __init__(self, reply="", exit_code=0, finish_reason="stop", tokens_out=100):
        self.reply = reply
        self.exit_code = exit_code
        self.finish_reason = finish_reason
        self.tokens_out = tokens_out
        self.task_name = None
        self.calls = 0

    def write_task(self, task_dir, name, body):
        self.task_name = name
        p = Path(task_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        self._task_path = p
        return str(p)

    def run_task(self, task_file, **kw):
        self.calls += 1
        return {"exit_code": self.exit_code, "stdout_tail": self.reply,
                "text": self.reply, "finish_reason": self.finish_reason,
                "tokens_out": self.tokens_out, "tokens": 200, "requests": 1,
                "error": "" if self.exit_code == 0 else "模拟失败"}


def build_root(tmp: Path, chapter_text: str, chapter=1):
    """造一个最小工作区：一章正文 + 设定 + 大纲目录。"""
    (tmp / "data" / "chapters" / "refined").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "outline").mkdir(parents=True, exist_ok=True)
    chap = tmp / "data" / "chapters" / "refined" / ("ch%02d.md" % chapter)
    chap.write_text(chapter_text, encoding="utf-8")
    (tmp / "data" / "setting" / "setting.json").write_text("{}", encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text("# 大纲", encoding="utf-8")
    return chap


CH1 = """# 第一章 起点

她推开门，风灌进来。

桌上那盏灯还亮着，灯芯结了花。

"你来晚了。"老人没回头。

她站在原地，很久没有说话。
"""


def main():
    print("=" * 70)
    print("派单执行：一次调用改多段")
    print("=" * 70)

    from utils import dispatch_run as dr

    # ---------- [1] 段号解析：变形要认 ----------
    print("\n[1] 段号解析：变形要认，越界要丢")
    raw = """===SEGMENT: 1===
第一段改写。

===SEGMENT: #3===
第三段改写。

===SEGMENT: ９===
全角数字九（越界，丢弃）

===SEGMENT: 2===
第二段改写。
"""
    taken, missing, extra = dr.parse_segments(raw, [1, 2, 3, 4])
    check(set(taken) == {1, 2, 3}, "认出了 1/2/3 三段（含 #3 变形）", list(taken))
    check(missing == [4], "缺的 4 段如实报 missing（不猜、不补）", missing)
    # 全角 ９ 不是合法段号 → 它既不在 taken 也不该冒充 9；allowed 里没有 9，
    # 所以要么进 extra 要么被忽略，但绝不能显示成段 9 的内容
    check(9 not in taken, "全角段号不冒充合法段", taken.keys())
    check(taken.get(1, "").startswith("第一段改写"), "段 1 正文正确归位", taken.get(1))
    check(taken.get(2, "").startswith("第二段改写"), "段 2 正文正确归位", taken.get(2))

    # ---------- [2] 越界段号绝不塞给邻近段 ----------
    print("\n[2] 越界段号：丢弃，不塞给邻近段（错位=毁正文）")
    raw2 = """===SEGMENT: 1===
这是段一的改写。

===SEGMENT: 99===
这是段99的改写（该被丢弃）
"""
    taken2, missing2, extra2 = dr.parse_segments(raw2, [1, 2])
    check(99 not in taken2, "段 99 不在候选里", list(taken2))
    check(99 in extra2, "段 99 被记进 extra（可追溯）", extra2)
    check(missing2 == [2], "段 2 缺失（没有被 99 的内容顶上）", missing2)
    check("99" not in str(taken2.get(2, "")), "段 2 没被越界内容污染", taken2.get(2))

    # ---------- [3] 包裹剥除 ----------
    print("\n[3] 模型爱加的代码围栏 / 整体引号要剥掉")
    raw3 = "===SEGMENT: 0===\n```\n她说：“走吧。”\n```\n"
    t3, _, _ = dr.parse_segments(raw3, [0])
    check(t3.get(0) == "她说：“走吧。”", "围栏被剥掉、正文里的中文引号保留", repr(t3.get(0)))
    raw4 = '===SEGMENT: 0===\n“整段被引号包住”\n'
    t4, _, _ = dr.parse_segments(raw4, [0])
    check(t4.get(0) == "整段被引号包住", "整体包裹的引号被剥掉", repr(t4.get(0)))

    # ---------- [4] 全链路：成功路径 ----------
    print("\n[4] 全链路（假 client）：候选落盘，正文一字不动")
    tmp = Path(tempfile.mkdtemp(prefix="nf_dsp_ok_"))
    chap = build_root(tmp, CH1)
    before = chap.read_bytes()
    reply = """===SEGMENT: 1===
风从门缝里钻进来，她把衣领拢了拢。

===SEGMENT: 2===
那盏灯还亮着，灯芯结了花。
"""
    fake = FakeClient(reply=reply, tokens_out=321)
    res = dr.run_batch({}, {}, 1, [1, 2], client=fake, root=str(tmp),
                       task_dir=str(tmp / "data" / "state" / "tasks"))
    check(res.get("ok") is True, "调用成功", res.get("message"))
    check(len(res.get("items") or []) == 2, "产出 2 条候选", len(res.get("items") or []))
    check(chap.read_bytes() == before, "**正文逐字节未变**（派单绝不直接写正文）")
    # 候选落盘可读回
    dpath = tmp / "data" / "state" / "dispatch" / "ch01.json"
    check(dpath.exists(), "候选文件已落盘")
    if dpath.exists():
        data = json.loads(dpath.read_text(encoding="utf-8"))
        paras = [it["para"] for it in data["items"]]
        check(sorted(paras) == [1, 2], "候选段号 = [1,2]", paras)
        check(all(it["status"] == "open" for it in data["items"]),
              "候选状态都是 open（等作者逐条采用）")

    # ---------- [5] 截断 → 整批作废 ----------
    print("\n[5] 截断（finish_reason=length）→ 整批作废，候选一条都不许进库")
    tmp2 = Path(tempfile.mkdtemp(prefix="nf_dsp_trunc_"))
    chap2 = build_root(tmp2, CH1)
    before2 = chap2.read_bytes()
    fake2 = FakeClient(reply="===SEGMENT: 1===\n半截", exit_code=2,
                       finish_reason="length")
    res2 = dr.run_batch({}, {}, 1, [1, 2], client=fake2, root=str(tmp2),
                        task_dir=str(tmp2 / "data" / "state" / "tasks"))
    check(res2.get("ok") is False, "截断被判失败", res2.get("message"))
    check("截断" in str(res2.get("message")), "失败原因说清是截断", res2.get("message"))
    check(not (tmp2 / "data" / "state" / "dispatch" / "ch01.json").exists(),
          "候选文件没被创建（整批作废）")
    check(chap2.read_bytes() == before2, "正文未变")

    # ---------- [6] 回复里一个段号都认不出 → 不写候选 ----------
    print("\n[6] 回复里认不出段号 → 不写候选（不许猜哪段是哪段）")
    tmp3 = Path(tempfile.mkdtemp(prefix="nf_dsp_none_"))
    build_root(tmp3, CH1)
    fake3 = FakeClient(reply="我改好了，但没说哪一段。")
    res3 = dr.run_batch({}, {}, 1, [1], client=fake3, root=str(tmp3),
                        task_dir=str(tmp3 / "data" / "state" / "tasks"))
    check(res3.get("ok") is False, "判失败", res3.get("message"))
    check(not (tmp3 / "data" / "state" / "dispatch" / "ch01.json").exists(),
          "没有候选落盘")

    # ---------- [7] 段数上限 ----------
    print("\n[7] 段数上限：撞上限直接拒（防截断）")
    tmp4 = Path(tempfile.mkdtemp(prefix="nf_dsp_max_"))
    build_root(tmp4, CH1)
    too_many = list(range(dr.MAX_SEGMENTS_PER_CALL + 1))
    res4 = dr.run_batch({}, {}, 1, too_many, client=FakeClient(), root=str(tmp4),
                        task_dir=str(tmp4 / "data" / "state" / "tasks"))
    check(res4.get("ok") is False, "超上限被拒", res4.get("message"))
    check("%d" % dr.MAX_SEGMENTS_PER_CALL in str(res4.get("message")),
          "错误里写清上限是多少", res4.get("message"))

    # ---------- [8] 越界段号 -> 拒（在调用之前） ----------
    print("\n[8] 段号越界：调用之前就拒（不白烧 token）")
    tmp5 = Path(tempfile.mkdtemp(prefix="nf_dsp_oob_"))
    build_root(tmp5, CH1)
    fake5 = FakeClient(reply="===SEGMENT: 0===\nx")
    res5 = dr.run_batch({}, {}, 1, [99], client=fake5, root=str(tmp5),
                        task_dir=str(tmp5 / "data" / "state" / "tasks"))
    check(res5.get("ok") is False, "越界被拒", res5.get("message"))
    check(fake5.calls == 0, "**没有发起模型调用**", "calls=%d" % fake5.calls)

    # ---------- [9] dry_run 不调用 ----------
    print("\n[9] dry_run：只出任务书，不调用模型")
    tmp6 = Path(tempfile.mkdtemp(prefix="nf_dsp_dry_"))
    build_root(tmp6, CH1)
    fake6 = FakeClient()
    res6 = dr.run_batch({}, {}, 1, [1], client=fake6, root=str(tmp6),
                        task_dir=str(tmp6 / "data" / "state" / "tasks"), dry_run=True)
    check(res6.get("ok") is True, "dry_run 成功", res6.get("message"))
    check(fake6.calls == 0, "没调用模型", "calls=%d" % fake6.calls)

    # ---------- [10] 任务书里带上了修改意见 ----------
    print("\n[10] 任务书：修改意见按段带上")
    tmp7 = Path(tempfile.mkdtemp(prefix="nf_dsp_note_"))
    build_root(tmp7, CH1)
    fake7 = FakeClient(reply="===SEGMENT: 1===\n改好了。")
    dr.run_batch({}, {}, 1, [1], notes={1: "把风声写具体一点"}, client=fake7,
                 root=str(tmp7), task_dir=str(tmp7 / "data" / "state" / "tasks"))
    task_text = fake7._task_path.read_text(encoding="utf-8") if hasattr(fake7, "_task_path") else ""
    check("把风声写具体一点" in task_text, "意见进任务书")
    check("===SEGMENT: 1===" in task_text, "任务书里带段号标记（模型照着答）", )

    print("\n" + "=" * 70)
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
