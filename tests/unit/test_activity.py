# -*- coding: utf-8 -*-
"""执行方进度（过程可见）的判据：看得见“现在在做什么、停了多久”。

## 为什么有这条

作者原话：“踢一脚跑一下 —— AI 干活时你看不见它走到哪步、卡在哪。”
`progress.json` 只有阶段状态，没有当前动作，更没有“多久没动静”。

## 判据（每条都会失败）

A. 报一步之后读得到：动作类型翻译成人话 + 目标（“写产物 · 第 3 章”）
B. **超时由读方算**：把心跳时间造旧 5 分钟 → `stale=True`、`idle_seconds` 在合理区间
   （不是写入方自述“我还在跑”）
C. 最近动作只留 50 条（长跑不能把文件撑大）
D. 端点：`GET /activity` 可读、`POST /activity/beat` 可写（agent 也要能写 —— 这是记录不是审批）
E. 没有任何心跳时也要返回合法结构（界面不能崩）

零 LLM、零费用，全部在临时目录。
用法：python tests/unit/test_activity.py
"""
import io
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    from utils import activity as A

    print("【E】还没有心跳 → 结构合法，界面不崩")
    tmp = Path(tempfile.mkdtemp(prefix="nf_act_"))
    st = A.status(tmp)
    check("ok 且 current 为空", st.get("ok") is True and st.get("current") is None, st)
    check("有一句人话说明", "还没有" in (st.get("summary") or ""), st.get("summary"))
    check("不会误报超时", st.get("stale") is False)

    print("\n【A】报一步 → 读得到人话")
    A.beat(tmp, "write", "第 3 章", "写 raw/03.md")
    st = A.status(tmp)
    check("当前动作是写第 3 章", st["current"]["target"] == "第 3 章", st["current"])
    check("动作类型翻成人话", st["summary"] == "写产物 · 第 3 章", st["summary"])
    check("刚报完不算超时", st["stale"] is False, st["idle_seconds"])

    print("\n【B】超时由读方算（不是自己说自己还活着）")
    old = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    A.beat(tmp, "read", "设定集", at=old)
    st = A.status(tmp)
    check("标为可能卡住", st["stale"] is True, st)
    check("停了约 5 分钟（280~320 秒）",
          st["idle_seconds"] is not None and 280 <= st["idle_seconds"] <= 320,
          st["idle_seconds"])
    check("摘要仍说清在做什么", st["summary"] == "读资料 · 设定集", st["summary"])

    print("\n【C】最近动作只留 50 条")
    for i in range(60):
        A.beat(tmp, "write", "第 %d 章" % i)
    data = A.load(tmp)
    check("落盘条数 = 50", len(data["recent"]) == 50, len(data["recent"]))
    check("留下的是最近的（最后一条是第 59 章）",
          data["recent"][-1]["target"] == "第 59 章", data["recent"][-1])

    print("\n【D】端点：读得到、agent 也写得进")
    import nf_api
    from nf_api_domains import activity as dom
    tmp2 = Path(tempfile.mkdtemp(prefix="nf_act_api_"))
    nf_api._set_root(tmp2)
    try:
        code, p = dom.handle_activity_get(None)
        check("GET /activity 200", code == 200 and p.get("ok") is True, (code, p))
        code, p = dom.handle_activity_beat(None, {"kind": "check", "target": "第 1 章"})
        check("POST /activity/beat 200", code == 200 and p.get("ok") is True, (code, p))
        code, p = dom.handle_activity_get(None)
        check("写完立刻读得到", (p.get("current") or {}).get("target") == "第 1 章", p.get("current"))
        from utils import agent_guard as ag
        check("报进度不在 agent 禁用名单里（它是记录，不是审批）",
              not ag.is_forbidden_in_agent_mode("/activity/beat"))
    finally:
        nf_api._set_root(ROOT)

    print("\n【F】自动上报与主动出声（防回潮）")
    src4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("stage4 写作时会自动报进度", "_act.beat(" in src4 and '第 %d 章' in src4)
    check("自动上报有节流（同章 30 秒一条）",
          "_BEAT_STATE" in src4 and ">= 30" in src4)
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("前端把超时转成系统通知", "notifyGate" in app and "maybeNotifyStale" in app)
    check("通知有节流（10 分钟）", "10 * 60 * 1000" in app)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
