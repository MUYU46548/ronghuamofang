# -*- coding: utf-8 -*-
"""事件分类表归作者（J22，2026-11-07）。

## 为什么有这条用例

作者原话：“判定勾选是用户做，不是开发的时候写死。”
改之前，勾选事件时“它改变了什么”的四个选项（人物处境/信息占有/关系/目标）
**写死在两处源码**：`utils/judge/records.py` 的 `EVENT_CLASSES` 与
`JudgeEvents.vue` 的 `CLASSES`。作者想加一类只能改代码 —— 那就是开发替作者
定判断维度，而判定引擎的产品口径恰恰是“机器摆事实、作者决断”。

现在的口径：
- 分类表是口径表里的一条**数据**（J22），GUI 可增删改，改一次产一个新版本；
- 判定写入只认表里的名字，不在表里 → 400，不静默丢；
- 老口径（没有 J22）暂用出厂四类，但 `source=legacy` 会一路传到界面，明说“还没登记”。

用法：python tests/unit/test_judge_event_classes.py
"""
import io
import re
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.judge import caliber as C          # noqa: E402
from utils.judge import records as R          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:240]) if detail else ""))


def _factory_doc():
    return {"version": 1, "fingerprint": C.fingerprint(C.FACTORY),
            "criteria": [dict(c) for c in C.FACTORY], "history": []}


def _legacy_doc():
    """v1 老口径：没有 J22（2026-11-07 之前的工作区就是这种）。"""
    return {"version": 1, "fingerprint": "x",
            "criteria": [dict(c) for c in C.FACTORY if c["id"] != "J22"],
            "history": []}


def case_factory_has_table():
    print("\n【1】出厂口径里有 J22，且四个默认分类齐全、不重名")
    by_id = {c["id"]: c for c in C.FACTORY}
    check("出厂表有 J22", "J22" in by_id)
    if "J22" not in by_id:
        return
    c = by_id["J22"]
    keys = [x["key"] for x in c["value"]]
    check("默认四类", keys == ["人物处境", "信息占有", "关系", "目标"], keys)
    check("每一类都有例子", all(x.get("eg") for x in c["value"]))
    check("consumer=author.events（唯一硬门）", c["consumer"] == "author.events", c["consumer"])
    check("产出方=作者（机器不代勾）",
          c["producer"] == "author" and c["settled_by"] == "author",
          (c["producer"], c["settled_by"]))


def case_read_paths():
    print("\n【2】读：正常态走口径表；老口径标 legacy 并给厂四类")
    classes, src = C.event_classes(_factory_doc())
    check("口径表 → source=caliber", src == "caliber", src)
    check("四个分类名来自表里", [x["key"] for x in classes][:2] == ["人物处境", "信息占有"], classes)
    legacy, src2 = C.event_classes(_legacy_doc())
    check("老口径 → source=legacy（界面必须说出来）", src2 == "legacy", src2)
    check("老口径暂用出厂四类", [x["key"] for x in legacy] == C.LEGACY_EVENT_CLASSES, legacy)


def case_update_validation():
    print("\n【3】改：非法分类表一律拒收（空表 / 重名 / 空名字）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        C.save_raw(root, _factory_doc())
        v0 = C.load(root)["version"]

        ok, d = C.update(root, {"J22": []}, by="作者")
        check("空表被拒", not ok and "至少" in d.get("error", ""), d.get("error"))
        ok, d = C.update(root, {"J22": [{"key": "关系"}, {"key": "关系"}]}, by="作者")
        check("重名被拒", not ok and "重复" in d.get("error", ""), d.get("error"))
        ok, d = C.update(root, {"J22": [{"key": "  "}]}, by="作者")
        check("空名字被拒", not ok and "不能为空" in d.get("error", ""), d.get("error"))
        ok, d = C.update(root, {"J22": "时间"}, by="作者")
        check("非列表被拒", not ok, d.get("error"))
        check("被拒时不产新版本", C.load(root)["version"] == v0, C.load(root)["version"])

        new = [{"key": "人物处境", "eg": "被抓"},
               {"key": "时间", "eg": "过了一年 / 换季"},
               {"key": "关系", "eg": "结盟"}]
        ok, d = C.update(root, {"J22": new}, by="作者")
        check("作者加一类“时间”可保存", ok and d.get("version") == v0 + 1, d)
        classes, src = C.event_classes(C.load(root))
        check("读回来就是作者那张表",
              [x["key"] for x in classes] == ["人物处境", "时间", "关系"], classes)
        check("改后 source 仍是 caliber", src == "caliber", src)
        hist = C.load(root).get("history") or []
        check("旧版本进 history（可回退）", bool(hist), len(hist))


def case_write_path():
    print("\n【4】写：判定只认表里的分类；表外的明确报错（不静默丢）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        C.save_raw(root, _factory_doc())
        # 表里有 → 收
        rec = R.set_events(root, 1, [0], {"0": "关系"}, evidences={"0": "他把杯子摔了。"},
                           total=2)
        check("表内分类被接收", rec["classes"].get("0") == "关系", rec.get("classes"))
        check("勾了+分类+证据 → 计入一件", rec["counted_events"] == 1, rec)
        # 表里没有 → 抛错（旧实现是静默过滤，少算一件还不吭声）
        raised = ""
        try:
            R.set_events(root, 1, [0], {"0": "时间"}, evidences={"0": "x"})
        except ValueError as e:
            raised = str(e)
        check("表外分类被拒并说明去哪加", "事件分类表" in raised, raised)
        # 作者已经把“时间”加进表 → 同一次写入就成立
        ok, _ = C.update(root, {"J22": [{"key": "时间", "eg": "换季"}]}, by="作者")
        rec = R.set_events(root, 2, [0], {"0": "时间"}, evidences={"0": "入秋了。"})
        check("加进表后同一分类可写", ok and rec["counted_events"] == 1, rec)


def case_gui_wiring():
    print("\n【5】界面：分类选项由接口送进来，不再在组件里写死")
    je = (ROOT / "console" / "src" / "JudgeEvents.vue").read_text(encoding="utf-8")
    jp = (ROOT / "console" / "src" / "JudgePanel.vue").read_text(encoding="utf-8")
    cb = (ROOT / "console" / "src" / "ChapterBlueprint.vue").read_text(encoding="utf-8")
    check("JudgeEvents 收到 classes prop", re.search(r"classes:\s*\{\s*type:\s*Array", je) is not None)
    check("JudgeEvents 走 computed 而非写死数组", "const CLASSES = computed" in je)
    check("判定页把 event_classes 传进去", "detail.event_classes" in jp)
    check("分章页把 event_classes 传进去", "judgeClasses.value = r.data.event_classes" in cb)
    check("改标准页有分类表编辑器", "addEventClass" in jp and "removeEventClass" in jp)
    # 反证：把“只写死四类”的写法放回去，上面的判据应能识别
    fake = 'const CLASSES = [\n  { key: "人物处境" },\n];'
    check("反证：写死数组时 computed 判据必红", "const CLASSES = computed" not in fake)


def main():
    case_factory_has_table()
    case_read_paths()
    case_update_validation()
    case_write_path()
    case_gui_wiring()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
