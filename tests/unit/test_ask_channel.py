# -*- coding: utf-8 -*-
"""提问通道的 API 判据：老用法不破、新用法（选项/默认/效果）能用、agent 不能代答。

## 为什么单独一份

`test_agent_collab_http.py` 用真服务测老链路（字符串选项 + 自由回答），
那份必须一直绿 —— 它是"从没被新功能搞坏"的底线。
这里补 2026-11-12 新增的那半：`{value,label,note}` 选项、`default`、`effect`、
以及"答重写会真的进待重写清单"。

## 判据（每条都会失败）

A. 对象选项 + default 能登记（回 id，pending 计数对）
B. default 不在选项里 → 400；选项缺 value → 400
C. 点选项作答（value）→ 200，回带回执；选项外的 value → 400
D. effect=rewrite_chapter + rewrite → 落到 data/state/rewrite_queue.json
E. 同一条问题内容变了 → 老答案作废并重开（拿旧答案套新问题 = 静默失真）
F. 权限：`/ask/answer` 在 agent 禁用名单，`/ask` / `/ask/pending` 不在

用法：python tests/unit/test_ask_channel.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


class FakeHandler:
    def __init__(self, query=""):
        self.path = "/ask" + (("?" + query) if query else "")

    def _query(self):
        import urllib.parse
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        return urllib.parse.parse_qs(qs)


def main():
    import nf_api
    from nf_api_domains import ask as dom
    from utils import ask as store

    tmp = Path(tempfile.mkdtemp(prefix="nf_askapi_"))
    nf_api._set_root(tmp)
    try:
        print("【A】对象选项 + default 能登记")
        code, p = dom.handle_ask(FakeHandler(), {
            "question": "第 2 章对话太多 —— 怎么办？",
            "context": "对话段占比 0.52（上限 0.35）",
            "options": [
                {"value": "rewrite", "label": "重写这一章", "note": "进待重写清单"},
                {"value": "keep", "label": "保留不改"},
            ],
            "default": "keep", "effect": "rewrite_chapter",
            "stage": 4, "chapter": 2, "source": "structure", "id": "api:ch2",
        })
        check("200 且回 id", code == 200 and p.get("id") == "api:ch2", (code, p))
        check("pending 计数 1", p.get("pending") == 1, p.get("pending"))
        code, pend = dom.handle_ask_pending(FakeHandler())
        item = (pend.get("items") or [{}])[0]
        check("选项带 label/note 存下来了",
              item.get("options", [{}])[0].get("label") == "重写这一章"
              and item.get("default") == "keep", item.get("options"))
        check("依据（context）也存下来了",
              "0.52" in (item.get("context") or ""), item.get("context"))

        print("\n【B】参数校验")
        code, p = dom.handle_ask(FakeHandler(), {
            "question": "q", "options": [{"value": "a"}], "default": "zzz"})
        check("default 不在选项里 → 400",
              code == 400 and "default" in str(p.get("error")), (code, p.get("error")))
        code, p = dom.handle_ask(FakeHandler(), {"question": "q", "options": [{"label": "无值"}]})
        check("选项缺 value → 400",
              code == 400 and "value" in str(p.get("error")), (code, p.get("error")))

        print("\n【C】点选项作答")
        code, p = dom.handle_ask_answer(FakeHandler(), {"id": "api:ch2", "value": "rewrite"})
        check("200 且回带回执", code == 200 and p.get("ok"), (code, p))
        check("effect 回带（界面能提示会做什么）",
              p.get("effect") == "rewrite_chapter", p.get("effect"))
        code, pend = dom.handle_ask_pending(FakeHandler())
        check("答过就不在待答清单", pend.get("count") == 0, pend.get("count"))
        code, ans = dom.handle_ask_answers(FakeHandler())
        check("/ask/answers 看得到（Agent 读这条）",
              ans.get("count") == 1 and ans["items"][0]["answer"] == "rewrite", ans)
        code, p = dom.handle_ask_answer(FakeHandler(), {"id": "api:ch2", "value": "zzz"})
        check("选项外的 value → 400 且列出可选",
              code == 400 and "rewrite" in str(p.get("error")), (code, p.get("error")))

        print("\n【D】答“重写”真的进待重写清单")
        check("rewrite_queue 里有第 2 章", store.queue_rewrites(tmp) == [2],
              store.queue_rewrites(tmp))
        dom.handle_ask_answer(FakeHandler(), {"id": "api:ch2", "value": "keep"})
        check("改选保留 → 出队", store.queue_rewrites(tmp) == [], store.queue_rewrites(tmp))

        print("\n【E】内容变了 → 老答案作废并重开")
        dom.handle_ask(FakeHandler(), {
            "question": "第 2 章对话太多 —— 换个问法：要不要重写？",
            "options": [{"value": "rewrite", "label": "重写"}, {"value": "keep", "label": "保留"}],
            "default": "keep", "effect": "rewrite_chapter",
            "stage": 4, "chapter": 2, "source": "structure", "id": "api:ch2"})
        code, pend = dom.handle_ask_pending(FakeHandler())
        check("重新出现在待答清单", pend.get("count") == 1, pend.get("count"))
        it = (pend.get("items") or [{}])[0]
        check("旧答案被清空（不拿旧答案套新问题）",
              it.get("answer") is None and it.get("status") == "pending", it.get("answer"))

        print("\n【F】权限")
        from utils import agent_guard as ag
        check("/ask/answer 在 agent 禁用名单（不能自问自答）",
              ag.is_forbidden_in_agent_mode("/ask/answer"))
        check("/ask 不在名单（Agent 就该用它）",
              not ag.is_forbidden_in_agent_mode("/ask"))
        check("/ask/pending 不在名单（界面要轮询）",
              not ag.is_forbidden_in_agent_mode("/ask/pending"))
    finally:
        nf_api._set_root(ROOT)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
