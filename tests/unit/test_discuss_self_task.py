# -*- coding: utf-8 -*-
"""`engine: self` 下的讨论轮：出任务书 → 收编（C13，2026-11-16）。

## 为什么这条必须有

self 引擎的承诺是"程序不调用任何模型"。讨论轮要模型，这条路径就必须退化成
**任务书**（零 token），而不是抛 500、也不是偷偷按量跑。
收编时的校验必须与真跑**同一份**（parse_turn + verify_evidence）——
preview 一套、真跑一套，等于给两个真相。

## 判据（每条都能变红）

- T1 任务书真生成，含请求全文、JSON 契约、产物路径
- T2 没有产物时 settle 返回 None（不凭空造一轮）
- T3 产物合法 → 收编成第 N 轮，settled 标记落盘
- T4 引文一条都不中 → missing_quote，**不落盘**
- T5 已收编的产物重复点击 → 不多加一轮（幂等）
- T6 端点两段式：第一次 202+task，写产物后第二次 200+settled

用法：python tests/unit/test_discuss_self_task.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api                                                # noqa: E402
from utils import discuss_ai, discussion                     # noqa: E402
from nf_api_domains import discuss_thread as dom             # noqa: E402

PASS, FAIL = [], []
TOPIC = "自任务讨论"
BODY = ("第 03 章 雨夜\n\n"
        "他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n")


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


class H:
    def __init__(self, headers=None):
        self.headers = headers or {}


def prep():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw" / "03.md").write_text(BODY, encoding="utf-8")
    for name in ("system.yaml", "project.yaml"):
        src = ROOT / "config" / name
        text = src.read_text(encoding="utf-8")
        if name == "system.yaml":
            import re
            text = re.sub(r"(?m)^engine:\s*\S+", "engine: self", text)
        (tmp / "config" / name).write_text(text, encoding="utf-8")
    return tmp


def good_payload():
    return {
        "text": "这一轮先说结论：冲突压在场面里，读者看不到代价。",
        "stance": "push",
        "evidence": [{"kind": "quote",
                      "quote": "他推门进来的时候，屋檐上的水线正连成了串。",
                      "why": "开场把人放在雨里"}],
        "decision": "代价要不要提前？",
        "action": {"kind": "suggest_edit", "target": {}, "why": "前置代价"},
        "needs_author": True,
    }


def main():
    print("=" * 66)
    print("self 引擎下的讨论轮（C13）")
    tmp = prep()
    orig_root = nf_api.ROOT
    nf_api.ROOT = tmp
    try:
        discussion.open_thread(3, TOPIC, root=str(tmp))
        md, js = discussion.thread_paths(3, TOPIC, str(tmp))

        print("\n【1】任务书")
        task = discuss_ai.emit_self_task(3, TOPIC, "往冲突想", root=str(tmp))
        text = task.read_text(encoding="utf-8")
        check("T1 任务书落盘", task.exists(), task)
        check("T1 在 tasks/self/discuss 下",
              "tasks/self/discuss" in task.as_posix().replace("\\", "/"), task.as_posix())
        check("T1 带本章正文", "屋檐上的水线正连成了串" in text)
        check("T1 带 JSON 契约", '"stance"' in text and '"evidence"' in text)
        check("T1 写清产物路径", ".ai.json" in text, text[-400:])
        check("T1 写清怎么放行", "再点一次" in text)

        print("\n【2】没有产物时不许凭空造一轮")
        check("T2 settle 返回 None", discuss_ai.settle_self(3, TOPIC, root=str(tmp)) is None)
        check("T2 讨论稿轮次仍是 0",
              (discussion._read_json(js).get("rounds") or 0) == 0)

        print("\n【3】收编合法产物")
        ap = discussion.ai_payload_path(3, TOPIC, str(tmp))
        ap.write_text(json.dumps(good_payload(), ensure_ascii=False), encoding="utf-8")
        res = discuss_ai.settle_self(3, TOPIC, root=str(tmp))
        check("T3 收编成第 1 轮", (res or {}).get("round") == 1, res)
        check("T3 讨论稿里出现了 AI 的话",
              "冲突压在场面里" in md.read_text(encoding="utf-8"))
        check("T3 引文校验有记录", ((res or {}).get("verify") or {}).get("verified") == 1, res)
        check("T3 产物标了 settled",
              json.loads(ap.read_text(encoding="utf-8")).get("settled") is True)

        print("\n【4】幂等：再点一次不多一轮")
        check("T5 已收编 → None", discuss_ai.settle_self(3, TOPIC, root=str(tmp)) is None)
        check("T5 轮次仍为 1", (discussion._read_json(js).get("rounds") or 0) == 1,
              discussion._read_json(js).get("rounds"))

        print("\n【5】引文一条都不中 → 不收编")
        bad = good_payload()
        bad["evidence"][0]["quote"] = "他推开门，看见雨里站着一个人。"
        ap.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        try:
            discuss_ai.settle_self(3, TOPIC, root=str(tmp))
        except discuss_ai.DiscussAIError as e:
            check("T4 missing_quote", e.kind == "missing_quote", e.kind)
        else:
            check("T4 missing_quote", False, "没有抛错")
        check("T4 讨论稿没有多一轮",
              (discussion._read_json(js).get("rounds") or 0) == 1,
              discussion._read_json(js).get("rounds"))
        ap.unlink()

        print("\n【6】端点两段式")
        code, data = dom.handle_discuss_ai_turn(H(), {"chapter": 3, "topic": TOPIC, "hint": ""})
        check("T6 第一次 → 202 + 任务书", code == 202 and data.get("mode") == "self"
              and bool(data.get("task")), (code, data))
        check("T6 返回里说清没调模型", "没有调用模型" in str(data.get("message")), data.get("message"))
        ap.write_text(json.dumps(good_payload(), ensure_ascii=False), encoding="utf-8")
        code, data = dom.handle_discuss_ai_turn(H(), {"chapter": 3, "topic": TOPIC, "hint": ""})
        check("T6 第二次 → 200 + 已收编",
              code == 200 and data.get("settled") is True, (code, data))
        check("T6 收编后轮次 = 2",
              (discussion._read_json(js).get("rounds") or 0) == 2,
              discussion._read_json(js).get("rounds"))
    finally:
        nf_api.ROOT = orig_root

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
