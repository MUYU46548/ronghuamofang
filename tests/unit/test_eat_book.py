# -*- coding: utf-8 -*-
"""吃书管线（SP-1024 移植）的确定性判据 —— 全程离线、零 token。

## 为什么这批断言长这样

吃书的每一段都是「模型产出 → 宽容解析 → 落盘 → 可续跑 → 只增不改并库」。
模型那头不可控，所以判据全部钉在**确定性半边**：
拆章正则、断点跳过、解析容错、合并语义、并库不覆盖、预演不动盘。

## 判据

A. 拆章：按 `第X回/章/卷` 切开、章名入 manifest、至少 2 个标题才拆
B. 断点：已有摘要不重调模型；一章失败不影响其它章，重跑只补失败那章
C. 解析容错：代码围栏 / 前后废话 / `{"角色": [...]}` 包裹都能收敛
D. 聚合：角色按名字合并、设定按设定名合并、时间线重排且不重复
E. 并库：只增不改（同名角色一个字都不动）、写前备份、dry-run 不落盘
F. 报告与成本：report.md 列章状态；cost.json 记 calls/tokens
用法：python tests/unit/test_eat_book.py
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

from utils.eatbook import core, runner                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


BOOK = """第一回 夜雨
他推开门，屋檐上的水线连成了串。

第二回 旧信
信上只有半个名字。

第三回 桥头
桥头蹲着两只石兽。
"""


def fake_call(prompt):
    """按提示词认出这是哪一步，返回该步的合法 JSON（故意夹带围栏/废话）。"""
    if "摘要员" in prompt:
        return ('```json\n{"章节": "x", "人物": [{"名字": "苏芷", "本章动作": "推门", '
                '"状态变化": "湿了衣角"}], "地点": ["客栈"], "事件": ["推门"], '
                '"时间线": "当夜", "伏笔动作": [{"类型": "埋设", "内容": "半张信"}], '
                '"专有名词": ["客栈"]}\n```')
    if "状态卡" in prompt and "角色" in prompt:
        return ('这是结果：\n[{"名字": "苏芷", "身份": "整理员", "性格特质": ["冷静"], '
                '"当前关系": [{"对象": "周砚清", "关系": "旧识", "依据章节": "第一回"}], '
                '"当前状态": "在查信", "出场章节": ["第一回"], "未决线": "信的另一半", '
                '"矛盾清单": []}]')
    if "世界观设定卡" in prompt:
        return ('{"设定卡": [{"设定名": "客栈", "类别": "地理", "内容": "河口镇唯一一家", '
                '"确立章节": ["第一回"], "补充章节": [], "矛盾点": []}]}')
    if "时间线" in prompt:
        return ('[{"顺序": 1, "时间": "当夜", "事件": "推门入客栈", "依据章节": "第一回", '
                '"支线": ""}, {"顺序": 1, "时间": "次日", "事件": "读信", '
                '"依据章节": "第二回", "支线": "主线"}]')
    if "伏笔" in prompt:
        return ('[{"伏笔": "半张信", "埋设位置": "第一回", "内容": "信只剩一半", '
                '"状态": "未回收", "回收位置": ""}]')
    if "一致性审计员" in prompt:
        return "无冲突"
    raise AssertionError("未识别的提示词：" + prompt[:60])


def build_camp(tmp: Path) -> Path:
    book = tmp / "old.txt"
    book.write_text(BOOK, encoding="utf-8")
    return runner.split_book(book, name="old", root=tmp / "eatbook")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="nf_eatbook_"))
    try:
        print("\n【A】拆章")
        camp = build_camp(tmp)
        man = runner.load_manifest(camp)
        check("拆出 3 章", len(man["chapters"]) == 3, len(man["chapters"]))
        check("章标题入 manifest", man["chapters"][0]["title"].startswith("第一回"),
              man["chapters"][0]["title"])
        check("章节文件真实存在",
              all((camp / c["file"]).exists() for c in man["chapters"]))
        check("首章内容包含正文开头",
              "屋檐上的水线" in (camp / man["chapters"][0]["file"]).read_text(encoding="utf-8"))
        dup = False
        try:
            runner.split_book(tmp / "old.txt", name="old", root=tmp / "eatbook")
        except ValueError:
            dup = True
        check("同名目录已存在时拒绝（不覆盖）", dup)
        nearly = tmp / "nearly.txt"
        nearly.write_text("只有一行\n", encoding="utf-8")
        few = False
        try:
            runner.split_book(nearly, name="nearly", root=tmp / "eatbook")
        except ValueError:
            few = True
        check("标题不足 2 个 → 拒绝（不把整本当一章）", few)

        print("\n【B】断点与失败隔离")
        calls = {"n": 0}

        def counting(prompt):
            calls["n"] += 1
            return fake_call(prompt)

        runner.run(camp, counting, stages=[2], log=lambda *_: None)
        first = calls["n"]
        check("首跑 3 章 = 3 次调用", first == 3, first)
        calls["n"] = 0
        runner.run(camp, counting, stages=[2], log=lambda *_: None)
        check("重跑不重调（断点生效）", calls["n"] == 0, calls["n"])

        # 第 2 章摘要删掉 + 模拟一次失败 → 只补那一章
        (camp / "summaries" / "0002.json").unlink()

        def flaky(prompt):
            calls["n"] += 1
            if "旧信" in prompt:
                raise RuntimeError("模拟模型抽风")
            return fake_call(prompt)

        calls["n"] = 0
        res = runner.run(camp, flaky, stages=[2], log=lambda *_: None)
        check("失败章不计入完成", not (camp / "summaries" / "0002.json").exists())
        check("失败被如实上报",
              any("0002" in f.lower() or "第 2 章" in f for f in res["failed"]),
              res["failed"])
        check("只重跑了缺的那一章", calls["n"] == 1, calls["n"])

        print("\n【C】解析容错")
        data = core.extract_json('前面有话\n```json\n{"a": 1}\n```\n后面还有话')
        check("围栏 + 前后废话可解析", data == {"a": 1}, data)
        wrapped = fake_call("角色状态卡")
        parsed = __import__("utils.eatbook.character_cards", fromlist=["x"]).parse_response(wrapped)
        check("③ 容忍「前面有话」+ 数组", parsed and parsed[0]["名字"] == "苏芷", parsed)
        wv = __import__("utils.eatbook.worldview", fromlist=["x"]).parse_response(
            fake_call("世界观设定卡"))
        check("⑨ 容忍 {\"设定卡\": [...]} 包裹", wv[0]["设定名"] == "客栈", wv)

        print("\n【D】聚合与合并")
        res = runner.run(camp, fake_call, stages=[2, 3, 6, 8, 9], log=lambda *_: None)
        chars = json.loads((camp / "characters.json").read_text(encoding="utf-8"))
        tls = json.loads((camp / "timeline.json").read_text(encoding="utf-8"))
        sets = json.loads((camp / "settings.json").read_text(encoding="utf-8"))
        check("角色卡落盘且不重复", len(chars) == 1 and chars[0]["名字"] == "苏芷", chars)
        check("时间线按序重排 1..n", [r["顺序"] for r in tls] == list(range(1, len(tls) + 1)), tls)
        check("设定卡落盘", sets and sets[0]["设定名"] == "客栈", sets)
        merged = runner.merge_rows([{"名字": "A", "身份": "旧"}],
                                   [{"名字": "A", "身份": "", "当前状态": "新"},
                                    {"名字": "B"}], "名字")
        check("同名合并：空字段不覆盖、非空覆盖",
              merged[0]["身份"] == "旧" and merged[0]["当前状态"] == "新", merged)
        check("新名字追加在末尾", [r["名字"] for r in merged] == ["A", "B"], merged)

        print("\n【E】并库只增不改")
        setting_p = tmp / "setting.json"
        setting_p.write_text(json.dumps({
            "characters": [{"name": "苏芷", "role": "（用户自己写的身份）"}],
            "world": {"locations": ["客栈：用户自己的描述"]},
            "plot_fragments": [], "timeline": []}, ensure_ascii=False), encoding="utf-8")
        before = setting_p.read_text(encoding="utf-8")
        dry = runner.merge_into_setting(camp, setting_p, dry_run=True)
        check("dry-run 不写盘", setting_p.read_text(encoding="utf-8") == before)
        check("dry-run 也如实报告要加什么",
              dry["added_characters"] == [] and dry["added_timeline"] >= 1, dry)
        real = runner.merge_into_setting(camp, setting_p, dry_run=False)
        after = json.loads(setting_p.read_text(encoding="utf-8"))
        check("同名角色一个字都不动",
              after["characters"][0]["role"] == "（用户自己写的身份）", after["characters"])
        check("同名地点不重复追加",
              after["world"]["locations"] == ["客栈：用户自己的描述"], after["world"])
        check("新时间线进 setting", len(after["timeline"]) == real["added_timeline"],
              after["timeline"])
        check("伏笔进 plot_fragments", len(after["plot_fragments"]) >= 1,
              after["plot_fragments"])
        hist = tmp / "history"
        check("写前留了备份", hist.is_dir() and any(hist.glob("setting_v*.json")),
              list(hist.glob("*")) if hist.is_dir() else None)

        print("\n【F】报告与成本")
        stats = {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                 "cost_yuan": 0.0, "model": "fake", "truncated": 0}
        runner.run(camp, fake_call, stages=[2, 3, 1, 7], stats=stats,
                   log=lambda *_: None)
        report = (camp / "report.md").read_text(encoding="utf-8")
        check("报告列了章状态", "第 1 章" in report and "✅" in report, report[:120])
        check("cost.json 落盘", (camp / "cost.json").exists())
        check("⑦ 病灶报告落盘", (camp / "cliche.json").exists())
        check("① 冲突文件落盘", (camp / "conflicts.json").exists())

        print("\n【G】CLI 预演零 token")
        sys.path.insert(0, str(ROOT / "scripts"))
        import eat_book
        code = eat_book.main(["run", str(camp), "--dry-run"])
        check("--dry-run 退 0", code == 0, code)
        code = eat_book.main(["status", str(camp)])
        check("status 退 0", code == 0, code)
        code = eat_book.main(["merge", str(camp)])
        check("merge 预演退 0（不带 --yes 不写盘）", code == 0, code)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
