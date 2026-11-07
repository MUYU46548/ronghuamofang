# -*- coding: utf-8 -*-
"""段落批注与“漂移”判定的回归锁（B5，2026-11-06）。

## 这个文件锁什么

批注此前**根本不落盘**（写完就交给模型，意见消失）。现在存“引用原文 + 段落号 +
前后文指纹”，所以必须验四种情况：

1. 正文没动 → 位置不变、不报警；
2. 前面插了一段 → 批注跟着原句走（段落号顺移），并标“位置变了”；
3. 原句被改了几个字 → 靠前后文指纹找回来，标“这一段被改过”；
4. 原句被删 → `status="missing"`（界面标红），**绝不悄悄指向别的段**。

用法：python tests/unit/test_annotations.py
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
                           ("  -> " + str(detail)[:200]) if detail else ""))


from utils import annotations as ann                            # noqa: E402

PARAS = ["她推门进来，雨水顺着伞骨滴在地上。",
         "他没有抬头，只把手里的账本合上。",
         "窗外有人喊了一声，声音很快被风吞掉。"]


def case_add_and_stable():
    print("\n【1】挂一条注 → 落盘；正文没动时不报警")
    tmp = Path(tempfile.mkdtemp(prefix="nf_ann_"))
    item = ann.add(tmp, 3, "这句要收住", 1, PARAS)
    check("有 id", bool(item.get("id")))
    check("记了引用原文", item["quote"] == PARAS[1])
    check("记了前后文指纹", bool(item["prev_fp"]) and bool(item["next_fp"]))
    check("文件落在 data/state/annotations/ch03.json",
          (tmp / "data" / "state" / "annotations" / "ch03.json").exists())
    d = ann.list_for_chapter(tmp, 3, PARAS)
    check("读回来 1 条、状态 open", d["total"] == 1 and d["annotations"][0]["status"] == "open")
    check("正文没动 → 不标漂移", d["annotations"][0]["drift"] is False, d["annotations"][0])


def case_insert_before():
    print("\n【2】前面插了一段 → 批注跟着原句走（段落号顺移）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_ann_"))
    ann.add(tmp, 3, "这句要收住", 1, PARAS)
    moved = ["新插进来的一段，跟这件事无关但确实占了位置。"] + PARAS
    d = ann.list_for_chapter(tmp, 3, moved)
    a = d["annotations"][0]
    check("段落号从 2 顺移到 3", a["para_index"] == 2, a["para_index"])
    check("标了“位置变了”", a["drift"] is True and "移到" in (a.get("why") or ""), a.get("why"))
    check("指向的仍是原来那句", a["current_text"] == PARAS[1], a["current_text"])


def case_quoted_text_edited():
    print("\n【3】原句被改了几个字 → 靠前后文指纹找回，并标“这一段被改过”")
    tmp = Path(tempfile.mkdtemp(prefix="nf_ann_"))
    ann.add(tmp, 3, "这句要收住", 1, PARAS)
    edited = list(PARAS)
    edited[1] = "他没有抬头，只把账本轻轻合上，手指在封皮上停了一瞬。"
    d = ann.list_for_chapter(tmp, 3, edited)
    a = d["annotations"][0]
    check("没有误判成“已删除”", a["status"] == "open", a["status"])
    check("位置仍是第 2 段", a["para_index"] == 1, a["para_index"])
    check("标了漂移（这一段被改过）", a["drift"] is True and "改过" in (a.get("why") or ""),
          a.get("why"))
    check("给出了现在这一段的内容（作者能接着判断）", edited[1][:8] in a["current_text"])


def case_quote_deleted():
    print("\n【4】原句被删 → status=missing（标红），绝不指到别的段")
    tmp = Path(tempfile.mkdtemp(prefix="nf_ann_"))
    ann.add(tmp, 3, "这句要收住", 1, PARAS)
    gone = [PARAS[0], PARAS[2]]
    d = ann.list_for_chapter(tmp, 3, gone)
    a = d["annotations"][0]
    check("状态 = missing", a["status"] == "missing", a["status"])
    check("计数里能看出失效几条", d["missing"] == 1, d)
    check("说清为什么（原句找不到）", "找不到" in (a.get("why") or ""), a.get("why"))


def case_resolve_and_remove():
    print("\n【5】标已处理 / 删除")
    tmp = Path(tempfile.mkdtemp(prefix="nf_ann_"))
    item = ann.add(tmp, 3, "这句要收住", 1, PARAS)
    check("resolve 返回 True", ann.resolve(tmp, 3, item["id"]) is True)
    d = ann.list_for_chapter(tmp, 3, PARAS)
    check("状态变 resolved（留痕不删）",
          d["annotations"][0]["status"] == "resolved", d["annotations"][0]["status"])
    check("remove 返回 True", ann.remove(tmp, 3, item["id"]) is True)
    check("删完是空的", ann.list_for_chapter(tmp, 3, PARAS)["total"] == 0)


def case_validation_and_endpoint():
    print("\n【6】参数校验 + 端点直调（空批注 / 越界段落 / 未知 action）")
    from nf_api_domains import chapters as dom_ch

    class H:
        headers = {}
        path = "/chapters/annotations?n=1"
        _query = lambda self: {"n": ["1"]}                        # noqa: E731

    tmp = Path(tempfile.mkdtemp(prefix="nf_ann_"))
    (tmp / "data" / "chapters" / "refined").mkdir(parents=True)
    (tmp / "data" / "chapters" / "refined" / "01.md").write_text(
        "## 第1章 x\n\n" + "\n\n".join(PARAS), encoding="utf-8")
    import nf_api
    old = nf_api.ROOT
    nf_api.ROOT = tmp
    try:
        code, _ = dom_ch.handle_chapters_annotations_set(H(), {"n": 1, "action": "add",
                                                               "para_index": 1, "note": ""})
        check("空批注 400", code == 400, code)
        code, _ = dom_ch.handle_chapters_annotations_set(H(), {"n": 1, "action": "add",
                                                               "para_index": 99, "note": "x"})
        check("段落越界 400", code == 400, code)
        code, _ = dom_ch.handle_chapters_annotations_set(H(), {"n": 1, "action": "nope"})
        check("未知 action 400", code == 400, code)
        code, payload = dom_ch.handle_chapters_annotations_set(
            H(), {"n": 1, "action": "add", "para_index": 0, "note": "开头要再慢一点"})
        check("合法挂注 200", code == 200 and payload.get("annotation"), (code, payload))
        code, payload = dom_ch.handle_chapters_annotations(H())
        check("GET 读回到 1 条且已重定位",
              code == 200 and payload["total"] == 1 and payload["relocated"] is True,
              (code, payload.get("total")))
    finally:
        nf_api.ROOT = old


def main():
    case_add_and_stable()
    case_insert_before()
    case_quoted_text_edited()
    case_quote_deleted()
    case_resolve_and_remove()
    case_validation_and_endpoint()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
