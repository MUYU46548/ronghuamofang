# -*- coding: utf-8 -*-
"""开局方案（动笔前定走向）的自检。

## 钉什么

1. **代价必填**：三个方案里只要有一个没写 `risk`，整批判失败 ——
   只写优点的卡等于没有选择，用户看不出取舍。
2. **截断必失败**：`finish_reason=length` 时不许把半截方案当成品（与全仓同一纪律）。
3. **选定才生效**：`pick` 之前 stage2 走 `user_outline`；`pick` 之后 stage2 的任务文本里
   必须出现方案名，且注明"冲突时以本节为准"。
4. **放弃可回退**：`clear` 之后 stage2 回到 `user_outline`。

用法：python tests/unit/test_open_variants.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import open_variants as OV                            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  <- " + str(detail)) if (detail and not cond) else ""))


GOOD = json.dumps({"variants": [
    {"title": "旧账重开", "hook": "他在道砟里捡到一块还在走的表。",
     "spine": "巡道工要查清姐姐的死，站长要守住二十年前的秘密。",
     "conflict": "外：有人在拆隧道；内：他宁可自己是错的。",
     "tone": "冷峻克制", "risk": "线索型悬疑最容易写成'查档案流水账'。",
     "chapters_hint": "一章捡表；二章对质；三章翻档案。"},
    {"title": "第一人称自白", "hook": "我知道那块表是我埋的。",
     "spine": "一个顶罪者在二十年里反复回到现场。",
     "conflict": "外：家属回来了；内：他想被认出来又怕被认出来。",
     "tone": "内省", "risk": "自白体容易变成情绪抒情，事件推进会被压扁。",
     "chapters_hint": "一章埋表；二章接待家属；三章失口。"},
    {"title": "双线并置", "hook": "1998 年的雪与 2018 年的雪同时落下。",
     "spine": "两个时间层的同一个人做出相反的选择。",
     "conflict": "外：事故真相；内：同一件事的两种记忆。",
     "tone": "对照", "risk": "双线一旦节奏不同步，读者会分不清哪条是现在。",
     "chapters_hint": "一章现在；二章过去；三章两条线撞上。"},
]}, ensure_ascii=False)


class FakeClient:
    def __init__(self, text, finish="stop"):
        self.text, self.finish = text, finish

    def _post_chat(self, messages, max_tokens=None):
        return self.text, {"prompt_tokens": 120, "completion_tokens": 300}, "fake-model", self.finish


PROJ = {"book": {"name": "铁轨尽头", "genre": "悬疑", "chapters": 6,
                 "target_words": 36000, "user_outline": "巡道工查姐姐的旧案。"}}


def main():
    print("== 开局方案 ==")
    tmp = Path(tempfile.mkdtemp(prefix="nf_variants_"))
    old_cwd = os.getcwd()
    try:
        (tmp / "data" / "setting").mkdir(parents=True)
        (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
            "characters": [{"name": "周砚清"}, {"name": "韩拙"}],
            "places": [{"name": "青石站"}],
        }, ensure_ascii=False), encoding="utf-8")

        # ---- 1. 设定摘要 ----
        print("\n[1] 设定摘要")
        digest = OV._digest(tmp / "data" / "setting" / "setting.json")
        check("摘要含角色与地点", "周砚清" in digest and "青石站" in digest, digest)
        check("摘要不超长", len(digest) < 200, len(digest))

        # ---- 2. 生成 ----
        print("\n[2] 生成三张卡")
        ok, msg, payload = OV.generate(PROJ, str(tmp / "data" / "setting" / "setting.json"),
                                      client=FakeClient(GOOD), root=str(tmp))
        check("生成成功", ok, msg)
        check("三张卡", len(payload["variants"]) == 3, len(payload["variants"]))
        check("每张都有 risk", all(v["risk"] for v in payload["variants"]),
              [v["title"] for v in payload["variants"]])
        check("落盘", (tmp / "data" / "outline" / "open_variants.json").exists())
        check("load_variants 可读回", len(OV.load_variants(str(tmp))["variants"]) == 3)

        # ---- 3. 三类失败 ----
        print("\n[3] 失败路径")
        no_risk = json.dumps({"variants": [
            {"title": "甲", "hook": "h", "spine": "s", "conflict": "c", "tone": "t", "risk": ""},
            {"title": "乙", "hook": "h", "spine": "s", "conflict": "c", "tone": "t", "risk": "有代价"},
        ]}, ensure_ascii=False)
        ok, msg, _ = OV.generate(PROJ, "x", client=FakeClient(no_risk), root=str(tmp))
        check("缺 risk 判失败", not ok and "risk" in msg, msg)

        ok, msg, _ = OV.generate(PROJ, "x", client=FakeClient(GOOD, finish="length"), root=str(tmp))
        check("截断判失败", not ok and "截断" in msg, msg)

        ok, msg, _ = OV.generate(PROJ, "x", client=FakeClient("这不是 JSON"), root=str(tmp))
        check("非 JSON 判失败", not ok, msg)

        # ---- 4. 选定 ----
        print("\n[4] 选定与放弃")
        ok, msg, rel = OV.pick(2, root=str(tmp))
        check("pick 成功", ok, msg)
        chosen_file = tmp / "data" / "outline" / "open_variant_chosen.md"
        check("chosen 文件写出", chosen_file.exists())
        text = chosen_file.read_text(encoding="utf-8")
        check("chosen 含方案名", "第一人称自白" in text, text[:80])
        check("chosen 含代价节", "已知代价" in text)
        check("chosen 写明优先级", "以本文件为准" in text)

        ok, msg, _ = OV.pick(99, root=str(tmp))
        check("pick 不存在的 id 失败", not ok and "99" in msg, msg)

        check("load_chosen 返回正文", "第一人称自白" in OV.load_chosen(str(tmp)))
        check("clear_chosen 删除", OV.clear_chosen(str(tmp)) is True)
        check("clear 后为空", OV.load_chosen(str(tmp)) == "")

        # ---- 5. GET 端点 ----
        print("\n[5] GET /outline/variants")
        import nf_api
        from nf_api_domains import outline as dom_outline

        class FakeH:
            def _query(self):
                return {}

        old_root = nf_api.ROOT
        try:
            nf_api.ROOT = tmp
            code, d = dom_outline.handle_open_variants_get(FakeH())
            check("返回 200 且 3 个方案", code == 200 and len(d["variants"]) == 3, d)
            check("未选定时 chosen=false", d["chosen"] is False)
            OV.pick(1, root=str(tmp))
            code, d = dom_outline.handle_open_variants_get(FakeH())
            check("选定后 chosen=true", d["chosen"] is True, d)
        finally:
            nf_api.ROOT = old_root

        # ---- 6. stage2 注入 ----
        print("\n[6] stage2 注入")
        shutil.copytree(ROOT / "prompts", tmp / "prompts")
        (tmp / "config").mkdir(exist_ok=True)
        (tmp / "config" / "project.yaml").write_text("book:\n  name: \"铁轨尽头\"\n",
                                                     encoding="utf-8")
        OV.clear_chosen(str(tmp))          # 上一节 pick 过，先回到"未选定"状态
        os.chdir(tmp)
        import stage2_outline as s2
        body_none = s2.build_task({}, PROJ)
        check("未选定时写「未选定开局方案」", "未选定开局方案" in body_none, body_none[:200])
        OV.pick(3, root=".")
        body_pick = s2.build_task({}, PROJ)
        check("选定后注入方案名", "双线并置" in body_pick, body_pick[:300])
        check("注入节写明优先级", "冲突时以本节为准" in body_pick)
        check("注入含代价", "节奏不同步" in body_pick)
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
