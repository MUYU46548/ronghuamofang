# -*- coding: utf-8 -*-
"""上下文包 + 正文设定名高亮（2026-11-05，SP-1026 工作台收尾）。

## 要治的病

1. **上下文包**：写作时到底注入了什么，此前只有日志里一行
   `[stage4] 第N章记忆：...`。作者要的“命中卡数 / 开伏笔 / 字数预算”在客户端里
   一个都看不到 —— 于是无法判断“这章写得差是不是因为没给它设定”。
2. **设定名高亮**：读正文时看到人名，要切到设定页签再搜一遍。SP-1026 的工作台
   做法是正文里可点、点开就是卡。

## 判据

- C1 `chapter_pack` 用**同一份 memory_policy** 的统计（不另算一套）
- C2 开伏笔只数“本章之前埋的”（未来的钩子是剧透，不是记忆）
- C3 章大纲缺失时给 hint，不抛异常、不编数字
- C4 `snapshot` 的上下文来源是盘上字数（不是“估算注入量”）
- C5 端点三处接线 + 只读（不进 Agent 禁止名单）
- C6 编辑器有阅读模式：点名字出卡，**且编辑态仍用 textarea**（不换富文本）
- C7 单字名不参与高亮（否则“风/雨”会把正文切碎）
反证：面板显示的是自己估的数、或点名字没反应 = 未完成。

用法：python tests/unit/test_context_pack_and_name_highlight.py
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

PASS, FAIL = [], []

SETTING = {
    "characters": [
        {"name": "岑砚", "aliases": ["柯砚"], "role": "主角",
         "traits": ["话少"], "relations": [{"with": "矢车", "desc": "邻居"}]},
        {"name": "矢车", "role": "邻居", "traits": ["失声"]},
    ],
    "world": {
        "locations": [{"name": "盐埕码头", "desc": "夜航船靠岸的地方"}],
        "items": [{"name": "青石", "desc": "能存一句话"}],
    },
    "plot_fragments": [
        {"summary": "岑砚替老太太把一句话压进青石，条件是许听不许问。"},
    ],
}

CH1 = """## 第1章 第一单

- 核心事件：岑砚在盐埕码头接第一单，把老太太的一句话压进青石，全程塞着耳朵。
- 涉及角色：岑砚；老太太
- 小目标：把这单做完且不听内容。
- 悬念钩子：谁有本事替他在盐课的登记簿上签名，而且签得跟他一模一样。
- 功能：建置
"""

CH2 = """## 第2章 追问

- 核心事件：矢车追问岑砚把青石放去了哪里，两人在码头对峙，最后不欢而散。
- 涉及角色：岑砚；矢车
- 小目标：问出石头的去向。
- 悬念钩子：石头自己响的那一夜，究竟是谁在数数。
- 功能：升级
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    print("=" * 70)
    print("上下文包 + 设定名高亮")
    print("=" * 70)

    from utils import context_report as cr

    tmp = Path(tempfile.mkdtemp(prefix="nf_ctx_"))
    try:
        (tmp / "data" / "setting").mkdir(parents=True)
        (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
        (tmp / "data" / "state" / "tasks" / "self" / "stage4").mkdir(parents=True)
        (tmp / "data" / "setting" / "setting.json").write_text(
            json.dumps(SETTING, ensure_ascii=False), encoding="utf-8")
        (tmp / "data" / "outline" / "chapters" / "01.md").write_text(CH1, encoding="utf-8")
        (tmp / "data" / "outline" / "chapters" / "02.md").write_text(CH2, encoding="utf-8")
        (tmp / "data" / "state" / "tasks" / "self" / "stage4" / "ch03.md").write_text(
            "任务书" * 100, encoding="utf-8")

        print("\n[C1] 上下文包复用 memory_policy")
        pack = cr.chapter_pack(tmp, 2)
        s = pack.get("summary") or {}
        check("有 summary", bool(s), list(s)[:6])
        check("命中卡数 > 0（plot_fragments 命中人名）", s.get("recalled", 0) > 0, s.get("recalled"))
        check("四维分列给出来了", isinstance(s.get("by_dim"), dict), s.get("by_dim"))
        check("字数预算来自 memory_policy 默认值",
              (s.get("budget") or {}).get("rolling_chars") == 2600, s.get("budget"))
        check("已分配字数有 L2/L3 两项",
              set((s.get("allocated") or {}).keys()) == {"rolling", "recall"},
              s.get("allocated"))
        check("章大纲字数被读到", pack.get("outline_chars", 0) > 50, pack.get("outline_chars"))

        print("\n[C2] 开伏笔只数本章之前的")
        check("第2章能数到第1章的钩子", s.get("open_foreshadows", 0) >= 1, s.get("open_foreshadows"))
        pack1 = cr.chapter_pack(tmp, 1)
        s1 = pack1.get("summary") or {}
        check("第1章看不到第2章才埋的钩子",
              s1.get("open_foreshadows", 0) <= s.get("open_foreshadows"),
              (s1.get("open_foreshadows"), s.get("open_foreshadows")))

        print("\n[C3] 缺章大纲时不编数字")
        pack9 = cr.chapter_pack(tmp, 9)
        check("给出 hint", "没有第 9 章" in (pack9.get("hint") or ""), pack9.get("hint"))
        check("不抛异常，仍有 summary", bool(pack9.get("summary")))

        print("\n[C4] snapshot 是盘上字数")
        snap = cr.snapshot(tmp)
        names = {x["name"]: x for x in snap["sources"]}
        check("设定集字数 > 0", names.get("设定集", {}).get("chars", 0) > 0)
        check("缺失来源标 exists=False", names.get("滚动摘要", {}).get("exists") is False)
        check("任务书体积统计到",
              snap["task_book"]["files"] == 1 and snap["task_book"]["bytes"] > 0,
              snap["task_book"])
        check("note 说清不是注入量", "不是一次请求的注入量" in snap["note"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[C5] 端点接线（三处）+ 只读")
    misc = (ROOT / "scripts" / "nf_api_domains" / "misc.py").read_text(encoding="utf-8")
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    guard = (ROOT / "scripts" / "utils" / "agent_guard.py").read_text(encoding="utf-8")
    check("域模块有 handler", "def handle_chapters_context(h)" in misc)
    check("ROUTES 登记", '"/chapters/context", handle_chapters_context' in misc)
    check("分发器转发", 'elif p == "/chapters/context"' in api)
    check("只读端点不进 Agent 禁止名单", "/chapters/context" not in guard)

    print("\n[C6] 阅读模式（点名字看卡）")
    editor = (ROOT / "console" / "src" / "ChapterEditor.vue").read_text(encoding="utf-8")
    desk = (ROOT / "console" / "src" / "ChapterDesk.vue").read_text(encoding="utf-8")
    check("有“阅读”按钮", ">阅读</button>" in editor)
    check("名字可点（ce-name + pickName）", "ce-name" in editor and "pickName" in editor)
    check("卡片在侧栏出现（ce-card）", "ce-card" in editor and "cardNow" in editor)
    check("编辑态仍是 textarea（没换富文本）", "class=\"ce-textarea\"" in editor)
    check("名字正则用 exec 循环拼段", "re.exec(p)" in editor)

    print("\n[C7] 单字名不高亮")
    check("长度 < 2 直接跳过", 'n.length < 2' in editor)

    print("\n[C8] 审阅台显示上下文包")
    check("调 /chapters/context", '"/chapters/context?n="' in desk)
    check("三读数：命中 / 开伏笔 / 预算", "命中卡" in desk and "开伏笔" in desk and "预算 L2" in desk)
    check("明细可展开", "看注入了哪几条" in desk)

    print("\n" + "=" * 70)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
