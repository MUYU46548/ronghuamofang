# -*- coding: utf-8 -*-
"""条目讨论：素材 / 设定条目上的商量与裁决（试跑第 16 条，2026-11-05）。

## 要治的病

作者原话：“想改素材，依然找不到任何讨论入口。我直接在 agent 里问了。”

章节侧有 DiscussThreadPanel，条目侧什么都没有。结论只留聊天里 = 没留。

## 判据

- E1 开题 → 发言 → 定稿：状态从 open 变 decided，轮次只追加
- E2 空定稿（一条都没写）被拒 —— 空结论不算结论
- E3 同名不同类别互不干扰（characters:岑砚 ≠ world:岑砚）
- E4 写盘前留历史备份，损坏的旧文件不炸（回落空表）
- E5 接线：三个 POST + 一个 GET；decide 进 FORBIDDEN_IN_AGENT_MODE（Agent 不能代拍）
- E6 界面：设定行/素材行各有“讨论”按钮，抽屉里明说“结论不进提示词”

用法：python tests/unit/test_entry_discuss.py
"""
import io
import shutil
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
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 70)
    print("条目讨论（试跑第 16 条）")
    print("=" * 70)

    from utils import entry_discuss as ed

    tmp = Path(tempfile.mkdtemp(prefix="nf_ed_"))
    try:
        print("\n[E1] 开题 → 发言 → 定稿")
        ok, res = ed.open_thread(tmp, "characters", "岑砚", "年龄要不要改大两岁")
        check("开题成功", ok and res["thread"]["status"] == "open", res.get("error"))
        ok2, _ = ed.open_thread(tmp, "characters", "岑砚", "再开一次")
        check("同一条不能重复开题", not ok2)
        ed.say(tmp, "characters", "岑砚", "author", "我想让他更沉稳一点")
        ok3, r3 = ed.say(tmp, "characters", "岑砚", "ai", "可以改到三十二，但要回改第 3 章的年龄锚")
        check("发言追加", ok3 and r3["rounds"] == 2, r3)
        ok4, r4 = ed.decide(tmp, "characters", "岑砚", {"summary": "改为三十二岁，并在第 3 章加一句年龄锚"})
        check("定稿成功", ok4 and r4["decision"]["summary"], r4)
        th = ed.get(tmp, "characters", "岑砚")
        check("状态变 decided", th["status"] == "decided", th["status"])
        check("轮次仍完整", len(th["rounds"]) == 2, len(th["rounds"]))

        print("\n[E2] 空定稿被拒")
        ed.open_thread(tmp, "world", "盐埕", "要不要改名")
        ok5, r5 = ed.decide(tmp, "world", "盐埕", {})
        check("一条都不写 → 拒", (not ok5) and "不算结论" in r5.get("error", ""), r5)

        print("\n[E3] 同名不同类别互不干扰")
        ed.decide(tmp, "world", "盐埕", {"must_not": "不改成盐场"})
        a = ed.get(tmp, "characters", "岑砚")
        b = ed.get(tmp, "world", "盐埕")
        check("两条各有各的结论",
              a["decision"]["summary"].startswith("改为") and
              b["decision"]["must_not"] == "不改成盐场", (a["decision"], b["decision"]))
        lst = ed.list_threads(tmp)
        check("列表两条", len(lst) == 2, len(lst))
        check("列表带类别中文名", {x["kind_label"] for x in lst} == {"角色", "世界观"},
              [x["kind_label"] for x in lst])

        print("\n[E4] 历史备份 + 坏文件不炸")
        hist = list((tmp / "data" / "state" / "entry_discuss_history").glob("*.json"))
        check("写盘前留了历史", len(hist) >= 1, len(hist))
        (tmp / "data" / "state" / "entry_discuss.json").write_text("{坏 json", encoding="utf-8")
        check("坏文件读成空表不抛", ed.list_threads(tmp) == [])
        ok6, _ = ed.open_thread(tmp, "materials", "素材01.md", "这份素材要不要拆")
        check("坏文件后仍能写", ok6)

        print("\n[E5] 接线与守卫")
        dom = (ROOT / "scripts" / "nf_api_domains" / "discuss.py").read_text(encoding="utf-8")
        api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        guard = (ROOT / "scripts" / "utils" / "agent_guard.py").read_text(encoding="utf-8")
        for ep in ("/discuss/entry/open", "/discuss/entry/say", "/discuss/entry/decide"):
            check("分发器接 %s" % ep, ('elif p == "%s"' % ep) in api)
        check("GET 列表接上", 'elif p == "/discuss/entry"' in api)
        check("ROUTES 四条", all(('"%s"' % p) in dom for p in
                                 ("/discuss/entry", "/discuss/entry/open",
                                  "/discuss/entry/say", "/discuss/entry/decide")))
        check("定稿进 Agent 禁止名单", '"/discuss/entry/decide"' in guard)

        print("\n[E6] 界面")
        app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
        comp = (ROOT / "console" / "src" / "EntryDiscuss.vue").read_text(encoding="utf-8")
        check("设定行有讨论按钮", "openEntryDiscuss(settingTab" in app)
        check("素材行有讨论按钮", "openEntryDiscuss('materials'" in app)
        check("抽屉明说结论不进提示词", "不会自动进章节写作提示词" in comp)
        check("有写回设定集按钮", "标为已采用" in comp and "标为已否决" in comp)
        check("写回走 /setting/status", '"/setting/status"' in comp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
