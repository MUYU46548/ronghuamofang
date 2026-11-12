# -*- coding: utf-8 -*-
"""产物闸的 API 判据：候选能列出来、作者能批，别人不能乱批。

## 为什么要有这一层

`product_gate` 是判据，界面要能按它做事就得有端点。这一层只做三件事：
列出候选、记批准、记驳回 —— 全部零 token。

## 判据（每条都会失败）

A. 有候选 → list 能列出来（含状态与"为什么在等"）
B. 没有候选 → list 空清单，不报错
C. approve → 状态转 approved，随后能合成到正式路径
D. **非登记路径 → 400**（不许通过 API 批准任意文件：那等于给了一把万能钥匙）
E. reject → 状态转 rejected，正式路径不动
F. 缺参 / 非法 action → 400 且说清

用法：python tests/unit/test_products_api.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []
REL = "data/setting/setting.json"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


class FakeHandler:
    def __init__(self, query=""):
        self.path = "/review/products/x" + (("?" + query) if query else "")


def main():
    import nf_api
    from nf_api_domains import products as dom

    tmp = Path(tempfile.mkdtemp(prefix="nf_prod_api_"))
    nf_api._set_root(tmp)
    try:
        print("【B】没有候选 → 空清单，不报错")
        code, p = dom.handle_products_list(FakeHandler())
        check("200 且 items 为空", code == 200 and p.get("ok") and p.get("items") == [], p)
        check("counts 全 0", all(v == 0 for v in (p.get("counts") or {}).values()),
              p.get("counts"))

        print("\n【A】有候选 → 列得出来，带状态与原因")
        pend = tmp / "data" / "_pending" / "stage1" / REL
        pend.parent.mkdir(parents=True, exist_ok=True)
        pend.write_text(json.dumps({"characters": [{"name": "甲"}]}), encoding="utf-8")
        code, p = dom.handle_products_list(FakeHandler())
        items = p.get("items") or []
        check("200 且列出一条", code == 200 and len(items) == 1, items)
        if items:
            check("状态 pending", items[0]["state"] == "pending", items[0])
            check("带候选路径（谁都能核）", items[0]["candidate"].endswith(REL),
                  items[0].get("candidate"))
            check("带原因（为什么在等）", bool(items[0].get("why")), items[0].get("why"))

        print("\n【D】非登记路径 → 400（不给万能钥匙）")
        code, p = dom.handle_products_decide(
            FakeHandler(), {"stage": 1, "rel": "config/system.yaml", "action": "approve"})
        check("拒绝批未登记文件", code == 400, (code, p.get("error")))
        code, p = dom.handle_products_decide(
            FakeHandler(), {"stage": 4, "rel": REL, "action": "approve"})
        check("拒绝批未接入的阶段", code == 400, (code, p.get("error")))

        print("\n【F】缺参 / 非法 action → 400")
        for name, body in [("缺 stage", {"rel": REL, "action": "approve"}),
                           ("缺 rel", {"stage": 1, "action": "approve"}),
                           ("非法 action", {"stage": 1, "rel": REL, "action": "maybe"})]:
            code, p = dom.handle_products_decide(FakeHandler(), body)
            check(name + " → 400", code == 400, (code, p.get("error")))

        print("\n【G】权限：批准只归界面，读清单不拦")
        from utils import agent_guard as ag
        check("decide 在 agent 禁用名单里",
              ag.is_forbidden_in_agent_mode("/review/products/decide"))
        check("list 不在禁用名单（agent 该知道有没有待批）",
              not ag.is_forbidden_in_agent_mode("/review/products/pending"))
        src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        check("nf_api 接了 list 分支", "dom_products.handle_products_list" in src)
        check("nf_api 接了 decide 分支", "dom_products.handle_products_decide" in src)

        print("\n【H】批准前先看一眼：候选正文读得出来（只读，不碰正式路径）")
        check("list 带人话阶段名", items and items[0].get("label") == "设定集",
              items[0].get("label") if items else None)
        q = "stage=1&rel=%s" % REL
        code, p = dom.handle_products_content(FakeHandler(query=q))
        check("200 且拿到正文", code == 200 and p.get("ok")
              and "characters" in (p.get("text") or ""), (code, p.get("error") or p.get("chars")))
        check("带字数（界面要说清看的是多大一份）",
              isinstance(p.get("chars"), int) and p["chars"] > 0, p.get("chars"))
        check("没越过候选区去读正式路径（正式文件当时还不存在）",
              not (tmp / REL).exists())
        code, _p = dom.handle_products_content(
            FakeHandler(query="stage=1&rel=config/system.yaml"))
        check("非清单路径 → 400（与批准同一个判据）", code == 400, code)
        code, _p = dom.handle_products_content(FakeHandler(query="rel=%s" % REL))
        check("缺 stage → 400", code == 400, code)
        check("content 不在 agent 禁用名单（只读）",
              not ag.is_forbidden_in_agent_mode("/review/products/content"))

        print("\n【C】approve → 状态转 approved，能合成")
        from utils import product_gate as pg
        code, p = dom.handle_products_decide(
            FakeHandler(), {"stage": 1, "rel": REL, "action": "approve"})
        check("批准 200", code == 200 and p.get("ok"), (code, p))
        check("状态 approved", pg.status(tmp, 1, REL)["state"] == "approved")
        ok, msg, _info = pg.promote(tmp, 1, REL)
        check("合成成功", ok is True, msg)
        check("正式文件已存在", (tmp / REL).exists())

        print("\n【E】reject → 状态 rejected，正式路径不动")
        pend.write_text(json.dumps({"characters": [{"name": "乙"}]}), encoding="utf-8")
        before = (tmp / REL).read_bytes()
        code, p = dom.handle_products_decide(
            FakeHandler(), {"stage": 1, "rel": REL, "action": "reject", "note": "这版不行"})
        check("驳回 200", code == 200 and p.get("ok"), (code, p))
        check("状态 rejected", pg.status(tmp, 1, REL)["state"] == "rejected")
        ok, msg, _info = pg.promote(tmp, 1, REL)
        check("驳回后不能合成", ok is False, msg)
        check("正式文件一个字节没动", (tmp / REL).read_bytes() == before)
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
