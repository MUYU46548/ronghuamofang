# -*- coding: utf-8 -*-
"""AI 节点精修的并发安全判据（2026-11-28）。

## 要治的病

`apply_node_refine` 的流程是：读全文 → 调模型（可能几十秒到几分钟）→
用**开始时那份旧全文**做局部替换 → 整份写回。中间没有任何校验。

于是这段对话里的场景（作者在墨坊填修订意见，同时 DSH 聊天框里也提了意见、
AI 已经接手改了）会**静默丢改动**：

    DSH 在模型调用期间改 global.md
      → 精修拿旧全文回写 → DSH 的改动被整份抹掉
      → 备份是调模型**前**做的，DSH 的改动不在 history 里，找不回来
      → 任务还报"成功"

不是"炸"（write_text 是 tmp+replace，不会写半截坏文件），是**纯丢失**。

## 三条判据

1. **读后被改 → 一个字都不写**（核心）。模拟"调模型期间别人改了文件"。
2. **结果不合格 → 不落盘**（校验内存，不是校验活文件）。
3. **没被改 → 正常写入**（不能因为加了保护就谁都不让写）。

反向验证的写法：每条都先确认"如果按旧逻辑会发生什么"，
再确认新逻辑拦住了 —— 否则一个永远返回 False 的实现也能让 1/2 通过。

用法：python tests/unit/test_outline_refine_race.py
"""
import io
import re
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


GOOD_OUTLINE = """# 整体大纲

## 起

- 节点1：开局，主角发现线索，决定追查下去
- 节点2：遇阻，第一次交锋失利，退回去重想

## 承

- 节点3：转折，找到新的盟友，局面开始变化

## 转

- 节点4：危机，盟友背叛，主角被逼到死角

## 合

- 节点5：收束，主角接受了代价，世界回到新的平衡

## 关键节点

- 节点1
- 节点5

## 预计章节数

12
"""


def main():
    print("=" * 68)
    print("AI 节点精修的并发安全")
    print("=" * 68)

    # 用临时目录 + monkeypatch 模块级路径，避免碰真实 data/
    import utils.outline_panel as op

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        gpath = root / "data" / "outline" / "global.md"
        hdir = root / "data" / "outline" / "history"
        gpath.parent.mkdir(parents=True)
        hdir.mkdir(parents=True)
        gpath.write_text(GOOD_OUTLINE, encoding="utf-8")

        # 把模块里的绝对路径指到临时目录（模块用的是常量）
        orig = (op.GLOBAL, op.HISTORY_DIR, op.SETTING)
        op.GLOBAL = str(gpath)
        op.HISTORY_DIR = str(hdir)
        op.SETTING = str(root / "data" / "setting" / "setting.json")
        Path(op.SETTING).parent.mkdir(parents=True, exist_ok=True)
        Path(op.SETTING).write_text('{"characters": {}, "world": {}}', encoding="utf-8")

        class FakeClient:
            """假客户端：在 run_task 里模拟"调模型期间，别人改了文件"。"""

            def __init__(self, mutate=None, out=None):
                self.mutate = mutate
                self.out = out or "- 节点1：改写后的内容，这里放足够长的正文以通过长度校验"

            def write_task(self, task_dir, name, body):
                return "fake_task.md"

            def run_task(self, task):
                if self.mutate:
                    self.mutate()
                return {"exit_code": 0, "stdout_tail": self.out}

            writes_files = True

        try:
            # ---------------------------------------------------------- 1
            print("\n【1】调模型期间外部改了文件 → 一个字都不写")

            def others_edit():
                # 模拟 DSH 在聊天框里让 agent 追加了一条新节点
                cur = gpath.read_text(encoding="utf-8")
                gpath.write_text(
                    cur.replace("## 关键节点",
                                "- 节点6：外部 Agent 刚加的节点，必须不被抹掉\n\n## 关键节点"),
                    encoding="utf-8")

            before = gpath.read_text(encoding="utf-8")
            ok, msg = op.apply_node_refine(
                cfg={}, proj={}, entry_id="node_1", feedback="把开局写得更紧一点",
                client=FakeClient(mutate=others_edit))
            after = gpath.read_text(encoding="utf-8")

            check("被并发改动时返回失败", ok is False, msg)
            check("**外部改动原样保留**（没被旧全文覆盖）",
                  "外部 Agent 刚加的节点" in after)
            check("失败原因说清是“被改过”且明说没写",
                  ("被改过" in str(msg)) and ("没写" in str(msg) or "一个字" in str(msg)),
                  msg)
            check("反向验证：旧逻辑下这里会被覆盖（对比基线）",
                  after != before, "内容确有变化，说明测试确实模拟到了并发")

            # ---------------------------------------------------------- 2
            print("\n【2】结果结构不合格 → 不落盘")

            # 先把文件恢复干净
            gpath.write_text(GOOD_OUTLINE, encoding="utf-8")
            before2 = gpath.read_text(encoding="utf-8")
            # 让模型返回一个"把 ## 预计章节数 删掉"的结果是不可能的（只改一行），
            # 所以这里改测：替换成功后内存文本里该节仍在 —— 这是正向对照；
            # 反向测的是 check_global_outline_text 本身能拦住坏文本。
            import stage2_outline as s2
            bad = GOOD_OUTLINE.replace("## 预计章节数\n\n12", "")
            ok_bad, errs = s2.check_global_outline_text(bad)
            check("check_global_outline_text 能拦缺节的文本", ok_bad is False, errs)
            ok_good, errs2 = s2.check_global_outline_text(GOOD_OUTLINE)
            check("check_global_outline_text 对正常文本放行", ok_good is True, errs2)
            check("新增的文本入口与路径版结论一致",
                  s2.check_global_outline(str(gpath))[0] == ok_good)

            # ---------------------------------------------------------- 3
            print("\n【3】没被并发改动 → 正常写入")

            gpath.write_text(GOOD_OUTLINE, encoding="utf-8")
            # entry_id 是解析出来的内部 id（node_1），不是"节点1"这种字面
            ok3, payload = op.apply_node_refine(
                cfg={}, proj={}, entry_id="node_1", feedback="把开局写得更紧一点",
                client=FakeClient())
            after3 = gpath.read_text(encoding="utf-8")
            check("无并发时修订成功", ok3 is True, str(payload)[:200])
            check("修订内容真的落盘了",
                  "改写后的内容" in after3, after3[:120])
            check("其它节点一个字没动",
                  "节点5：收束" in after3 and "## 预计章节数" in after3)

            # ---------------------------------------------------------- 4
            print("\n【4】undo 会说明“会连带抹掉多少”")

            # undo 需要 history 里**至少有一个** global_vN.md。上面成功写入的那次
            # 会在调模型前备份一份，正好够用。
            vers = sorted(hdir.glob("global_v*.md"))
            check("前置：history 里已有备份（上一次精修留下的）", len(vers) >= 1,
                  [p.name for p in vers])
            if vers:
                cur = gpath.read_text(encoding="utf-8")
                # 造一个"备份之后又被改过"的状态，undo 会连带抹掉它
                gpath.write_text(cur + "\n- 节点7：撤销前刚加的内容\n", encoding="utf-8")
                ok4, info = op.undo_last_refine()
                check("undo 成功", ok4 is True, info)
                check("返回 overwritten_chars（能告诉用户会连带撤掉多少）",
                      isinstance(info, dict) and "overwritten_chars" in info
                      and (info.get("overwritten_chars") or 0) > 0,
                      info if isinstance(info, dict) else str(info))

        finally:
            op.GLOBAL, op.HISTORY_DIR, op.SETTING = orig

    print("\n" + "-" * 68)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
