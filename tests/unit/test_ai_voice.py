# -*- coding: utf-8 -*-
"""去 AI 腔：构造样本校准（2026-11-07，B8 的“不等真实样本”那一条）。

作者原话：“书信/单元写法、散文校准、去 AI 腔你自己造样本。”
所以这里用 `examples/samples/ai_voice/` 的对照样本做校准：AI 腔样本必须被判“疑似”，
人写样本必须零命中；文体的三个样本（书信/单元/散文）也是零命中 —— 防止扫描器把
正常的书信体、一句一段判成 AI 腔。

用法：python tests/unit/test_ai_voice.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import ai_voice as AV          # noqa: E402

SAMPLES = ROOT / "examples" / "samples"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:240]) if detail else ""))


def _scan(rel):
    return AV.scan_file(SAMPLES / rel)


def case_ai_sample():
    print("\n【1】AI 腔样本必须判“疑似”，且命中至少两类（单类是正常中文也可能有的）")
    r = _scan("ai_voice/ai_01.md")
    check("样本可读", r.get("ok") is True, r.get("error"))
    check("判为疑似", r.get("suspect") is True, r.get("verdict"))
    check("命中 ≥2 类", r.get("feature_count", 0) >= 2, r.get("feature_count"))
    codes = {h["code"] for h in r.get("hits") or []}
    check("抓到解释性旁白", "explain_narration" in codes, codes)
    check("抓到模板套话", "cliche_phrase" in codes, codes)
    check("抓到对称排比", "symmetry" in codes, codes)
    check("每条命中带原文片段（可核对）",
          all(h.get("samples") for h in r.get("hits") or []),
          [h.get("samples") for h in r.get("hits") or []][:2])


def case_human_sample():
    print("\n【2】人写样本必须零命中（宁可漏报，不许误报）")
    r = _scan("ai_voice/human_01.md")
    check("不判疑似", r.get("suspect") is False, r.get("verdict"))
    check("零命中", r.get("feature_count") == 0,
          [(h["code"], h["samples"][:2]) for h in r.get("hits") or []])


def case_form_samples():
    print("\n【3】三种文体的样本也不许误报（书信的短句、散文的抒情不是 AI 腔）")
    for rel in ("forms/letter_01.md", "forms/episodic_01.md", "forms/essay_01.md"):
        r = _scan(rel)
        check("%s 零命中" % rel, r.get("feature_count") == 0,
              [(h["code"], h["samples"][:2]) for h in r.get("hits") or []])


def case_thresholds_and_report():
    print("\n【4】阈值只有一个出处；报告把“证据≠结论”说清楚")
    check("判定线是具名常量", AV.THRESHOLDS.get("suspect_feature_count") == 2,
          AV.THRESHOLDS)
    r = AV.scan_text("她感到一阵莫名的温暖。生活就是这样。")
    md = AV.report_markdown(r)
    check("报告列出命中类别", "解释性旁白" in md and "模板套话" in md, md[:120])
    check("报告明说“证据，不是结论”", "不是结论" in md, md[-80:])
    empty = AV.report_markdown(AV.scan_text("他把碗放进水池，水凉了也没换。"))
    check("零命中时也明说眼睛仍是第一位", "没有命中" in empty and "眼睛" in empty, empty)


def case_taskbook_wiring():
    print("\n【5】去 AI 腔的任务书里带机器扫描；别的模式不带（别把证据塞给感官扩写）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "data" / "chapters" / "raw").mkdir(parents=True)
        (root / "data" / "chapters" / "raw" / "01.md").write_text(
            "她感到一阵莫名的温暖。在这个快节奏的时代，生活就是这样。", encoding="utf-8")
        from utils import rewrite_studio as RS
        d1 = RS.build("dehumanize", 1, root=root)
        check("dehumanize 任务书生成", d1.get("ok") is True, d1)
        task = Path(d1["task"]).read_text(encoding="utf-8")
        check("任务书里有扫描段", "机器先扫了一遍" in task, task[:120])
        check("任务书里带原文片段", "她感到" in task, task[task.find("机器先扫"):][:200])
        check("任务书明说证据不是结论", "不是结论" in task)
        d2 = RS.build("sensory", 1, root=root)
        task2 = Path(d2["task"]).read_text(encoding="utf-8")
        check("sensory 不带扫描段", "机器先扫了一遍" not in task2, task2[:100])


def case_essay_calibration():
    print("\n【6】散文校准：样本按散文口径不判退化（叙事口径会把它判成“段落太少”）")
    from utils import verify_chapter as VC
    from utils import writing_form as WF
    text = (SAMPLES / "forms" / "essay_01.md").read_text(encoding="utf-8")
    app = WF.applicability(form="essay")
    check("散文关掉段落结构判据", app.get("para_structure") is False, app.get("para_structure"))
    degen, why = VC.check_degenerate(text, para_structure=app.get("para_structure"))
    check("散文样本按散文口径不判退化", not degen, why)
    degen_n, why_n = VC.check_degenerate(text, para_structure=True)
    check("反证：同一样本按叙事口径会被段落判据拦下（说明这条判据真的按文体走）",
          bool(degen_n), why_n)


def case_unreadable_sentences():
    print("\n【7】句法层“读不通”：抓得到结构缺件，且不碰腔调判定线")
    r = _scan("ai_voice/unreadable_01.md")
    codes = {h["code"]: h["samples"] for h in r.get("unreadable") or []}
    check("抓到碎句（单独成句的一个字）", "fragment_sentence" in codes,
          codes.get("fragment_sentence"))
    check("抓到半句话（引介语后面没有主句）", "dangling_connector" in codes,
          codes.get("dangling_connector"))
    check("半句话的原文带出来（作者能核对）",
          any("包括这条在内" in s for s in codes.get("dangling_connector") or []),
          codes.get("dangling_connector"))
    check("不进腔调判定线（样本 feature_count=0、suspect=False）",
          r.get("feature_count") == 0 and r.get("suspect") is False,
          (r.get("feature_count"), r.get("suspect")))
    # 能力边界：语义讲不通的句子机器抓不到 —— 这是如实声明，不是待办
    all_samples = [s for v in codes.values() for s in v]
    check("如实声明边界：语义讲不通的句子不在命中里（机器判不了）",
          not any("两条腿" in s for s in all_samples), all_samples)

    md = AV.report_markdown(r)
    check("报告把句法层单独成段", "句法层扫到读不通" in md, md[:120])
    check("报告说清语义层仍需人看", "机器判不了" in md, md[-120:])

    dlg = AV.scan_text("\u201c睡。\u201d他说完就背过身去。")
    check("对白里的单字不误报（那是人物说话，不是行文问题）",
          not (dlg.get("unreadable") or []),
          [(h["code"], h["samples"]) for h in dlg.get("unreadable") or []])


def case_unreadable_no_false_positive():
    print("\n【8】既有样本在句法层零命中（宁可漏报，不许误报）")
    for rel in ("ai_voice/human_01.md", "ai_voice/ai_01.md", "forms/letter_01.md",
                "forms/episodic_01.md", "forms/essay_01.md"):
        rr = _scan(rel)
        check("%s 句法层零命中" % rel, not (rr.get("unreadable") or []),
              [(h["code"], h["samples"]) for h in rr.get("unreadable") or []])


def case_book_scan():
    print("\n【9】全书扫描：层按 refined > checked > raw 取，三层并集不漏章，且不写盘")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel in ("raw", "checked", "refined"):
            (root / "data" / "chapters" / rel).mkdir(parents=True)
        # 第 1 章三层都有 → 只应读 refined；第 2 章只有 raw；第 3 章只有 checked
        (root / "data/chapters/raw/01.md").write_text("今晚不用消化这个。睡。", encoding="utf-8")
        (root / "data/chapters/checked/01.md").write_text("他把碗洗了。", encoding="utf-8")
        (root / "data/chapters/refined/01.md").write_text("他把碗洗了。水凉。", encoding="utf-8")
        (root / "data/chapters/raw/02.md").write_text("他站着。包括这在内，也是话。", encoding="utf-8")
        (root / "data/chapters/checked/03.md").write_text("他走了。", encoding="utf-8")

        before = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        rep = AV.scan_book(root)
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        layers = {c["chapter"]: c.get("layer") for c in rep["chapters"]}
        check("三层并集：1/2/3 章都在", sorted(layers) == [1, 2, 3], layers)
        check("有 refined 就读 refined", layers.get(1) == "refined", layers.get(1))
        check("只有 raw 的章也扫到", layers.get(2) == "raw", layers.get(2))
        check("只有 checked 的章也扫到", layers.get(3) == "checked", layers.get(3))
        total = sum(c.get("count") or 0 for c in rep["chapters"])
        check("命中数与逐章一致", rep["total"] == total, (rep["total"], total))
        check("只扫不写（盘上文件一个没变）", before == after)

        one = AV.scan_book(root, 2)
        check("?chapter=2 只扫一章", [c["chapter"] for c in one["chapters"]] == [2],
              [c["chapter"] for c in one["chapters"]])
        miss = AV.scan_book(root, 9)
        check("扫没有的章不抛错，如实说没有",
              miss["chapters"][0].get("error") is not None, miss["chapters"][0])


def main():
    case_ai_sample()
    case_human_sample()
    case_form_samples()
    case_thresholds_and_report()
    case_taskbook_wiring()
    case_essay_calibration()
    case_unreadable_sentences()
    case_unreadable_no_false_positive()
    case_book_scan()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
