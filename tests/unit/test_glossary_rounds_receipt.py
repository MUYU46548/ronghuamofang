# -*- coding: utf-8 -*-
"""口径表 / 轮次 / 外派回执（R4+R8 / R5 / R6，2026-11-16）。

## 三条缺口各自的判据

- **R4/R8 口径表**：把"名册 32/44/47"这种跨章错从"人肉 grep"变成一条机械判据；
  改一个数只改 glossary 一处；历法写法混用要能报出来。
- **R5 轮次**：一轮 = 一张判据表；`evaluate()` 要能回答"这一轮达标了吗、差在哪"。
- **R6 回执**：外派自报的数字不算数 —— 主控复算 sha256/汉字，不一致判失败。

## 判据（每条都能变红）

- G1 中文数字解析（十二/四十七/三千一百）
- G2 `value` 里的取值能抽成允许数字
- G3 与口径冲突的写法被点名（47 vs 允许 32/44），合规写法不报
- G4 `upsert_term` 改一处即生效，并留历史备份
- G5 历法：相对时间 / 天数写法 都能报
- G6 `scan_book` 出跨章汇总（第 8 章 47、第 10 章 32）
- G7 没有口径表 → 零 noise（不报错、不误报）
- R5a 出厂四轮在、判据键受校验
- R5b `evaluate` 达标/不达标都能变
- R5c 切轮次 / 未知轮次报错
- R6a 回执三件齐全 → ok
- R6b 自报汉字数被改 → mismatch（以脚本为准）
- R6c 没回执 → no_receipt（不算通过）
- R6d 回执说有产物、盘上没有 → mismatch
- R6e 体检只报结论（output 为空）→ mismatch
- R6f 任务书里写了回执契约

用法：python tests/unit/test_glossary_rounds_receipt.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import agent_receipt as ar            # noqa: E402
from utils import glossary as gl                # noqa: E402
from utils import rounds as rd                   # noqa: E402
from nf_api_domains import metrics as dom        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


GLOSSARY = {
    "terms": [
        {"term": "名册", "value": "32→44", "status": "locked", "anchor_chapter": 1},
        {"term": "阿丙", "value": "十二个", "status": "locked"},
    ],
    "calendar": {"era": "当地纪年 6362", "anchor": "赤阳异变（第 1 天）",
                 "style": "正文用“X月X日”", "relative_forbidden": True,
                 "prefer_absolute": True},
}


def main():
    print("=" * 66)
    print("口径表 / 轮次 / 回执（R4+R8 / R5 / R6）")
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "refined").mkdir(parents=True, exist_ok=True)

    print("\n【1】数字与口径（G1–G3）")
    check("G1 四十七 → 47", gl.cn_to_int("四十七") == 47, gl.cn_to_int("四十七"))
    check("G1 十二 → 12", gl.cn_to_int("十二") == 12)
    check("G1 三千一百 → 3100", gl.cn_to_int("三千一百") == 3100, gl.cn_to_int("三千一百"))
    nums = gl.term_numbers({"value": "12→32→44→48→47（存44亡3）"})
    check("G2 从 value 抽取值", {12, 32, 44, 48, 47} <= set(nums), nums)

    body_bad = ("他把名单摊开。\n\n名册上一共四十七人，他没说话。\n\n"
                "阿丙今年只有十二个。\n")
    rep = gl.check(body_bad, 8, GLOSSARY)
    kinds = {f["kind"] for f in rep["findings"]}
    # 这段里“阿丙今年只有十二个”同时踩了两条：口径（十二个合规）+ 历法（今年是相对时间）
    check("G3 两类命中都出现在同一章里",
          kinds == {"term_conflict", "calendar_mix"}, kinds)
    bad = [f for f in rep["findings"] if f.get("kind") == "term_conflict"]
    check("G3 47 与口径冲突被点名", any(f["found"] == 47 for f in bad), bad)
    check("G3 冲突里给出台号与原句",
          all(f.get("para_index") is not None and f.get("quote") for f in bad), bad[:1])
    check("G3 合规写法（十二个）不报", all(f.get("term") != "阿丙" for f in bad), bad)
    check("G3 冲突说明里写清允许值",
          all("44" in str(f.get("why")) for f in bad), [f.get("why") for f in bad][:1])

    body_cal = "去年冬天，他把伞借给了她。\n\n第三天，名单贴在墙上。\n"
    rep2 = gl.check(body_cal, 9, GLOSSARY)
    cal = [f for f in rep2["findings"] if f["kind"] == "calendar_mix"]
    check("G5 相对时间“去年”被点名", any("去年" in str(f.get("why")) for f in cal), cal)
    check("G5 天数写法与月日约定冲突被点名",
          any("第 N 天" in str(f.get("why")) for f in cal), cal)
    empty = gl.check(body_bad, 8, {"terms": [], "calendar": {}})
    check("G7 没有口径表 → 零 noise", empty["count"] == 0, empty)

    print("\n【2】写入与跨章汇总（G4/G6）")
    gl.save(GLOSSARY, str(tmp))
    p = gl.path(str(tmp))
    check("G4 口径表落盘", p.exists(), p)
    gl.upsert_term("名册", value="32→44→47", status="locked", numbers=[32, 44, 47], root=str(tmp))
    reloaded = gl.load(str(tmp))
    check("G4 改一处即生效", reloaded["terms"][0]["value"] == "32→44→47", reloaded["terms"][0])
    check("G4 写入留了历史备份",
          any((tmp / "data" / "setting" / "history").glob("glossary_*.yaml")))
    # 让 47 变成"合规"，再造一个跨章冲突：第 8 章 47、第 10 章 32（32 也在允许里）
    (tmp / "data" / "chapters" / "raw" / "08.md").write_text(
        "名册上一共四十七人，他没说话。\n", encoding="utf-8")
    (tmp / "data" / "chapters" / "raw" / "10.md").write_text(
        "名册上只写了三十二人。\n", encoding="utf-8")
    both = gl.load(str(tmp))
    for t in both["terms"]:
        if t["term"] == "名册":
            t["value"] = "44"
            t["numbers"] = [44]
    gl.save(both, str(tmp))
    scan = gl.scan_book(None, str(tmp))
    slot = (scan.get("by_term") or {}).get("名册") or {}
    check("G6 跨章汇总认出两个取值", set(slot.get("values") or {}) == {"47", "32"}, slot)
    check("G6 汇总里点出章号",
          "8" in (slot.get("summary") or "") and "10" in (slot.get("summary") or ""),
          slot.get("summary"))

    print("\n【3】轮次（R5）")
    data = rd.load(str(tmp))
    check("R5a 出厂四轮", [r["name"] for r in data["rounds"]] == ["骨架", "语体", "扩写", "润色"],
          data["rounds"])
    check("R5a 当前轮是骨架", rd.current(str(tmp))["name"] == "骨架")
    (tmp / "data" / "chapters" / "raw" / "03.md").write_text(
        "\n\n".join(["他把伞靠在门边，雨水顺着伞骨往下淌。" * 6] * 4), encoding="utf-8")
    strict = [{"name": "太严", "goal": "x", "checks": {"chapter_min": "99999"}},
              {"name": "太松", "goal": "y", "checks": {"chapter_min": "10"}}]
    rd.save(strict, "太严", str(tmp))
    bad = rd.evaluate(3, None, str(tmp))
    check("R5b 不达标能变红", bad["ok"] is False and "chapter_min" in bad["gaps"], bad)
    rd.set_current("太松", str(tmp))
    ok = rd.evaluate(3, None, str(tmp))
    check("R5b 达标能变绿", ok["ok"] is True, ok)
    check("R5b 判据行带实测值", ok["checks"][0]["actual"] > 0, ok["checks"])
    # 字数口径必须与体检面板一致（整份文件 vs 去标题/注释，实测差 12 字 → 界面打架）
    from utils import prose_metrics as _pm
    body_txt = (tmp / "data" / "chapters" / "raw" / "03.md").read_text(encoding="utf-8")
    check("R5b 字数与 metrics 同口径",
          ok["checks"][0]["actual"] == _pm.analyze(body_txt)["hanzi"],
          (ok["checks"][0]["actual"], _pm.analyze(body_txt)["hanzi"]))
    try:
        rd.set_current("不存在", str(tmp))
        check("R5c 未知轮次报错", False, "没抛错")
    except ValueError as e:
        check("R5c 未知轮次报错", "没有这一轮" in str(e), str(e))
    try:
        rd.save([{"name": "x", "checks": {"乱写的键": "1"}}], None, str(tmp))
        check("R5a 未知判据键被拒", False, "没抛错")
    except ValueError as e:
        check("R5a 未知判据键被拒", "不认识的判据键" in str(e), str(e))
    book = rd.evaluate_book("太松", None, str(tmp))
    check("R5b 整本汇总给出未达标章号", book["ok"] in (True, False) and "failed" in book, book)

    print("\n【4】外派回执（R6）")
    out = tmp / "data" / "chapters" / "raw" / "05.md"
    out.write_text("他推门进来的时候，水线正连成了串。\n", encoding="utf-8")
    chk = tmp / "check.txt"
    chk.write_text("verify_chapter: 字数 120，标点 0 错，退化 0\n", encoding="utf-8")
    ar.build_receipt("run1", ["data/chapters/raw/05.md"],
                     checks=[{"name": "verify_chapter", "output": chk.read_text(encoding="utf-8")}],
                     edits=[{"chapter": 5, "para_index": 2, "why": "并了两轮问答"}],
                     root=str(tmp))
    res = ar.verify("run1", str(tmp))
    check("R6a 三件齐全 → ok", res["ok"] is True, res)
    check("R6a 判据来源写清是脚本", res["trusted"] == "script", res.get("trusted"))
    check("R6a 产物指纹是真的（sha256 非空）",
          bool((res["outputs"] or [{}])[0].get("sha256")),
          res["outputs"])

    rcp = ar.receipt_path("run1", str(tmp))
    data2 = json.loads(rcp.read_text(encoding="utf-8"))
    data2["outputs"][0]["hanzi"] = 99999
    rcp.write_text(json.dumps(data2, ensure_ascii=False), encoding="utf-8")
    res2 = ar.verify("run1", str(tmp))
    check("R6b 自报汉字数被改 → mismatch",
          res2["ok"] is False and res2["verdict"] == "mismatch", res2["mismatches"])
    check("R6b 不一致时站在脚本一边", res2["trusted"] == "script")

    res3 = ar.verify("没这个run", str(tmp))
    check("R6c 没回执 → 不算通过", res3["verdict"] == "no_receipt" and res3["ok"] is False, res3)

    ar.build_receipt("run2", ["data/chapters/raw/99.md"], root=str(tmp))
    res4 = ar.verify("run2", str(tmp))
    check("R6d 回执说有、盘上没有 → mismatch", res4["ok"] is False, res4["mismatches"])

    ar.build_receipt("run3", ["data/chapters/raw/05.md"],
                     checks=[{"name": "ai_voice", "output": ""}], root=str(tmp))
    res5 = ar.verify("run3", str(tmp))
    check("R6e 体检只报结论不算体检",
          res5["ok"] is False and any(m.get("field") == "checks" for m in res5["mismatches"]),
          res5["mismatches"])

    import nf_agent_dispatch as disp
    brief = disp.build_task_brief("测试派单", "把第 3 章第 2 节补到 2200 字", run_id="runX")[1]
    text = Path(brief).read_text(encoding="utf-8")
    check("R6f 任务书里有回执契约", "runX_receipt.json" in text and "sha256" in text, text[-400:])
    check("R6f 任务书里给了生成回执的命令", "agent_receipt.py" in text)

    print("\n【5】路由接线")
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    for method, path, fn in (("GET", "/glossary", "handle_glossary"),
                             ("POST", "/glossary/check", "handle_glossary_check"),
                             ("GET", "/rounds", "handle_rounds"),
                             ("POST", "/rounds/evaluate", "handle_rounds_evaluate")):
        check("R5d ROUTES 有 %s %s" % (method, path),
              any(m == method and p == path for m, p, _ in dom.ROUTES))
        check("R5d 分发器有 %s" % fn, ("dom_metrics." + fn) in api_src)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
