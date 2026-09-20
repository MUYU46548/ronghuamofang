# -*- coding: utf-8 -*-
"""校对阶段（scripts/proofread.py）离线自检。

全部在临时项目根中运行，真实仓库零触碰：
- 造 3 章含"已知缺陷"的样章（标点/错字/格式/节奏）
- 断言各检查器都能命中，且干净文本不误报
- 断言报告双落盘（json + md）且 schema 可被 GUI 复用
- 断言 --llm 走 FakeClient 时能解析 stdout JSON 并叠加 finding

用法：python tests/test_proofread.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + msg)
    else:
        FAIL += 1
        print("  [FAIL] " + msg)


CH_CLEAN = """## 第一章 归途

雨下了一整夜。他推门进来，把伞靠在门边，看了一眼窗外的天色。

“回来了？”她问。

“嗯。”他把外套挂好，“路上堵。”

她没有再问，只是把炉火拨得更旺了些。
"""

CH_PUNCT = """## 第二章 裂痕

他说：“我明天就走。

夜色很深，仿佛一切都没有发生过....

她没有回答—只是看着窗外。

风从门缝里钻进来,带着潮气,冷得出奇。
"""

CH_TYPO = """## 第三章 旧信

他做为长子，既使心里不服，也只能按耐着性子点头。

这事迫不急待，不能再拖了。

信上的字迹陌生，他却跑的快，一下就追上了邮差。

她认真的看着他，没有说话。
"""

PAD = "他抬头看了看灰蒙蒙的天，雨丝斜斜地落下来，街上的人渐渐少了。"


def pad(text, target):
    """把样章填充到大致 target 字数（用于制造字数为离群章）。"""
    while len(text) < target:
        text += "\n\n" + PAD
    return text


def build_env(tmp):
    """搭一个最小临时项目根。"""
    (Path(tmp) / "data" / "chapters" / "refined").mkdir(parents=True)
    (Path(tmp) / "data" / "setting").mkdir(parents=True)
    (Path(tmp) / "data" / "outline").mkdir(parents=True)
    (Path(tmp) / "data" / "state" / "tasks").mkdir(parents=True)
    (Path(tmp) / "config").mkdir(parents=True)
    (Path(tmp) / "prompts").mkdir(parents=True)

    base = Path(tmp) / "data" / "chapters" / "refined"
    # 01/02 体量接近，03 明显偏短 → 制造一个可识别的字数离群章
    (base / "01.md").write_text(pad(CH_CLEAN, 800), encoding="utf-8")
    (base / "02.md").write_text(pad(CH_PUNCT, 2100), encoding="utf-8")
    (base / "03.md").write_text(CH_TYPO, encoding="utf-8")

    setting = {
        "characters": [
            {"name": "露汐", "aliases": ["小汐", "汐"], "description": "女主"},
            {"name": "沈砚", "aliases": ["砚哥"], "description": "男主"},
        ],
        "world": [], "plot_fragments": [], "timeline": [],
    }
    (Path(tmp) / "data" / "setting" / "setting.json").write_text(
        json.dumps(setting, ensure_ascii=False), encoding="utf-8")
    (Path(tmp) / "config" / "system.yaml").write_text(
        "chapter:\n  target_words:\n  - 200\n  - 2000\n", encoding="utf-8")
    # 拷贝真实提示词模板，验证 template_loader 路径
    src = ROOT / "prompts" / "stage5_proofread.md"
    shutil.copy(src, Path(tmp) / "prompts" / "stage5_proofread.md")


def main():
    tmp = tempfile.mkdtemp(prefix="nf_proofread_")
    old = os.getcwd()
    try:
        build_env(tmp)
        os.chdir(tmp)
        import proofread as pf

        print("\n[1] 标点检查")
        punct = pf.check_punctuation(CH_PUNCT, 2)
        kinds = {f["detail"] for f in punct}
        ok(any("不配对" in k for k in kinds), "命中引号不配对")
        ok(any("省略号" in k for k in kinds), "命中省略号不统一")
        ok(any("破折号" in k for k in kinds), "命中破折号单写")
        ok(any("半角标点" in k for k in kinds), "命中中英标点混用")
        ok(all(f["severity"] in ("error", "warn", "info") for f in punct), "severity 取值合法")

        print("\n[2] 错字检查")
        typo = pf.check_confusables(CH_TYPO, 3)
        details = " ".join(f["detail"] for f in typo)
        for wrong in ("做为", "既使", "按耐", "迫不急待"):
            ok(wrong in details, "命中错字「%s」" % wrong)
        ok(("跑的快" in details) or ("的/地/得" in details), "命中动词+的+补语（应为得）")
        ok(any("地" in f["suggestion"] for f in typo), "命中副词+的+动词（应为地）")
        ok(all(f["type"] == "typo" for f in typo), "type 均为 typo")

        print("\n[3] 误报防护")
        ok(not pf.check_confusables(CH_CLEAN, 1), "干净文本不报错字")
        punct_clean = pf.check_punctuation(CH_CLEAN, 1)
        ok(not [f for f in punct_clean if f["severity"] == "error"], "干净文本无 error 级标点问题")

        print("\n[4] 格式一致性")
        fmt = pf.check_format(CH_PUNCT + "他说１２３次，又说 123 次。", 2, pf.load_setting())
        ok(any("全角数字" in f["detail"] for f in fmt), "命中全半角数字混用")
        fmt_alias = pf.check_format("露汐看了小汐一眼，又看小汐。", 1, pf.load_setting())
        ok(any("多种称法" in f["detail"] for f in fmt_alias), "命中角色别名混用")
        fmt_title = pf.check_format("正文没有标题。", 9, {})
        ok(any("章节标题" in f["detail"] for f in fmt_title), "命中缺章节标题")

        print("\n[5] 节奏指标")
        m = pf.chapter_rhythm(CH_CLEAN)
        ok(m["word_count"] > 0 and m["paragraphs"] > 0, "字数/段数统计有效")
        ok(0.0 <= m["dialogue_ratio"] <= 1.0, "对话占比在 [0,1]")
        metrics = {1: {"word_count": 1000, "dialogue_ratio": 0.2},
                   2: {"word_count": 1050, "dialogue_ratio": 0.25},
                   3: {"word_count": 300, "dialogue_ratio": 0.9}}
        rhythm, rf = pf.analyze_rhythm(metrics)
        ok([o["n"] for o in rhythm["outliers"]] == [3], "只把明显偏短的 3 判为离群章")
        ok(any("离群" in f["detail"] for f in rf), "离群产出 finding")
        ok(any("波动" in f["detail"] for f in rf), "高 CV 产出 warning")

        print("\n[6] 主流程 + 报告落盘")
        ok_, msg = pf.run_proofread(use_llm=False, log=lambda *_: None)
        ok(ok_, "run_proofread 成功: " + msg)
        rp = Path("data/outline/proofread_report.json")
        md = Path("data/outline/proofread_report.md")
        ok(rp.exists() and md.exists(), "json + md 双落盘")
        rep = json.loads(rp.read_text(encoding="utf-8"))
        for k in ("generated_at", "scope", "summary", "rhythm", "chapters"):
            ok(k in rep, "报告含字段 %s" % k)
        ok(rep["scope"] == "refined", "scope 自动选中 refined")
        ok(rep["summary"]["chapters"] == 3, "统计到 3 章")
        ok(rep["summary"]["issues"] > 0, "统计到问题")
        ok(rep["rhythm"]["outliers"], "节奏指标里识别出离群章")
        ok(any(c["n"] is None for c in rep["chapters"]), "全书节奏 finding 已并入（chapter=None）")
        ok("校对报告" in md.read_text(encoding="utf-8"), "md 报告可读")
        ok("章节节奏" in md.read_text(encoding="utf-8"), "md 报告含节奏小节")

        print("\n[7] LLM 分支（FakeClient）")
        from utils.fake_client import FakeClient
        ok2, findings = pf.run_llm_proofread("\n\n".join(
            ["### 第%d章\n正文" % n for n in (1, 2)]), dry_run=False, client=FakeClient())
        ok(ok2, "LLM 校对（fake）成功")
        ok(len(findings) == 2, "fake 回放 2 条 finding（1/2 章各一条）")
        ok(all(f["source"] == "llm" for f in findings), "LLM finding 带 source=llm")
        task = Path("data/state/tasks/stage5_proofread_task.md")
        ok(task.exists(), "LLM 任务文件已写出")
        ok("设定集" in task.read_text(encoding="utf-8"), "任务文件注入了设定集")
        ok("校对" in task.read_text(encoding="utf-8"), "任务文件用了提示词模板")

        ok3, msg3 = pf.run_proofread(use_llm=True, client=FakeClient(), log=lambda *_: None)
        ok(ok3 and "LLM" in msg3, "主流程叠加 LLM 结果: " + msg3)
        rep2 = json.loads(rp.read_text(encoding="utf-8"))
        ok(rep2["use_llm"] is True, "报告标记 use_llm")
        ok(sum(len(c["findings"]) for c in rep2["chapters"]) > rep["summary"]["issues"],
           "LLM finding 已并入报告")

        print("\n[8] 无章节时的可行动报错")
        os.chdir(tempfile.mkdtemp(prefix="nf_empty_"))
        ok4, msg4 = pf.run_proofread(log=lambda *_: None)
        ok(not ok4 and "无可用章节目录" in msg4, "空项目返回可行动提示: " + msg4)
    finally:
        os.chdir(old)

    print("\n===== 校对自检: %d passed, %d failed =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
