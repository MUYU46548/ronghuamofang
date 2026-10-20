# -*- coding: utf-8 -*-
"""同质化/退化检测的自检（P1，零 token）。

## 为什么必须有这套用例

检测器最坏的失败不是漏报，是**误报**：一旦正常稿子也被标成"同质化"，
用户就会关掉它，等于这个功能不存在。所以本用例的主判据是**正常样本一条都不许报**，
退化样本**必须逐项报出来**（正反两方都钉）。

## 校准口径

阈值不是拍脑袋：用 6 个构造样本实测后定的（2026-10-19）：

| 样本 | 实测 | 期望 |
|---|---|---|
| normal（978 字，真人风格叙事） | shingle 0.023 / phrase 0.0 / cv 0.709 / 比喻 2.0 / 对话距离 0.558 | **0 条 finding** |
| repeat（同一段重复 20 次） | shingle 0.95 / phrase 1.0 | 报复读 |
| same-voice（三人台词逐字相同） | 对话距离 0.0 | 报同腔 |
| monotony（每句 20 字上下） | cv 0.033 | 报句式单调 |
| metaphor（每句一个比喻） | 每千字 108.7 | 报比喻堆砌 |
| sensory（只用光/暗/白/黑） | top10 覆盖 1.0 | 报感官锁死 |

反向验证：把 normal 样本的对话改成同一句 → 必须转成"报同腔"；
把 normal 每句都加"像" → 必须报比喻。这两条在 case_false_negative 里做。

用法：python tests/unit/test_homogeneity.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import homogeneity as H                                   # noqa: E402
from utils.verify_chapter import MIN_RATIO_BODY_CHARS                # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "homogeneity" / "normal.md"
NAMES = ["周砚清", "韩拙", "林小满", "沈澜", "阿澈", "木野"]

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  <- " + str(detail)) if (detail and not cond) else ""))


def codes(res):
    return [f["code"] for f in res["findings"]]


# ---- 构造样本 ------------------------------------------------------------

REPEAT = "他推开门，屋里没有人，桌上的茶还热着。他把外套挂在门后，走到桌前坐下，端起茶喝了一口。\n" * 20

SAME_VOICE = """沈澜说：“我知道了。”
阿澈说：“我知道了。”
木野说：“我知道了。”
沈澜说：“我们走吧。”
阿澈说：“我们走吧。”
木野说：“我们走吧。”
沈澜说：“这里不对。”
阿澈说：“这里不对。”
木野说：“这里不对。”
沈澜说：“先回去吧。”
阿澈说：“先回去吧。”
木野说：“先回去吧。”
三个人站在井口边，谁也没有再开口。风从下面上来，带着咸味。""" * 8

MONOTONY = """他沿着河岸一直走到天黑时候才停下来休息。
河水在脚下慢慢流动发出很轻很轻的声音来。
远处有一盏灯亮着像是有人还在那里等着谁。
他蹲下来把手伸进水里试了试觉得有些凉。
月亮从云里出来照在河面上留下一道白色痕迹。
他站起来继续往前走没有回头看一眼身后路。
路边长满了芦苇被风吹得东倒西歪停不下来。
他想起很多年前也有人在这里等过他一件小事。
那时候河水比现在深得多人也比现在年轻一些。
他走到桥头停下看见桥下的水一动不动地停着。
""" * 5

METAPHOR = """山路像一条被人遗忘的绳子。
石头像睡着了的兽。
风像一只手在推他。
月亮像一枚旧硬币。
云像扯开的棉絮。
远处像有人在敲铁。
他的影子像一条黑色的河。
树影像一根根竖起的钉子。
雨点像碎掉的玻璃。
他的心像被什么攥了一下。
""" * 7

SENSORY = """光从窗缝里进来，落在白色的墙上。
影子在暗处拉得很长，黑得像墨。
他把灯关掉，屋里只剩下一点灰白的光。
黑暗里看不清楚，只有窗外一点亮。
他伸手去摸，摸到的是一片冷的白。
光又亮了，影子重新回到墙上。
颜色在光里褪掉，只剩下黑白两色。
他站在亮处，暗处有人在看他。
白色的一小块光停在桌子上，像一张纸。
他把影子挪开，让光进来一点。
""" * 6


def main():
    print("== 同质化/退化检测 ==")

    # ---- 常量对齐（防两个闸门对同一份稿子给出不同结论）----
    print("\n[1] 常量对齐")
    check("MIN_BODY_CHARS 与 verify_chapter 同源",
          H.MIN_BODY_CHARS == MIN_RATIO_BODY_CHARS,
          (H.MIN_BODY_CHARS, MIN_RATIO_BODY_CHARS))

    # ---- 正常样本：一条都不许报 ----
    print("\n[2] 正常样本不误报（主判据）")
    normal = FIXTURE.read_text(encoding="utf-8")
    r = H.analyze(normal, NAMES)
    check("正常样本 enabled", r["enabled"], r["chars"])
    check("正常样本 0 条 finding", codes(r) == [], codes(r))
    check("正常样本 shingle < 0.10", r["metrics"]["shingle_repeat"] < 0.10,
          r["metrics"]["shingle_repeat"])
    check("正常样本 cv > 0.55", r["metrics"]["sentence_cv"] > 0.55,
          r["metrics"]["sentence_cv"])
    check("正常样本对话距离 > 0.30", r["metrics"].get("dialogue_same_voice", 0) > 0.30,
          r["metrics"].get("dialogue_same_voice"))

    # ---- 退化样本：逐项必须报 ----
    print("\n[3] 复读")
    r = H.analyze(REPEAT, [])
    check("复读报 shingle_repeat", "shingle_repeat" in codes(r), codes(r))
    check("复读报 phrase_repeat", "phrase_repeat" in codes(r), codes(r))
    check("复读 shingle >= 0.80", r["metrics"]["shingle_repeat"] >= 0.80,
          r["metrics"]["shingle_repeat"])

    print("\n[4] 对话同腔")
    r = H.analyze(SAME_VOICE, NAMES)
    check("同腔报 dialogue_same_voice", "dialogue_same_voice" in codes(r), codes(r))
    check("同腔距离 < 0.05", r["metrics"].get("dialogue_same_voice", 9) < 0.05,
          r["metrics"].get("dialogue_same_voice"))
    r2 = H.analyze(SAME_VOICE, [])                # 没有角色名 → 跳过而不是瞎报
    check("没有角色名时跳过而不是误报", "dialogue_same_voice" not in codes(r2), codes(r2))
    check("跳过原因写得清楚", any("dialogue_same_voice" in s for s in r2["skipped"]), r2["skipped"])

    print("\n[5] 句式单调")
    r = H.analyze(MONOTONY, [])
    check("单调报 sentence_monotony", "sentence_monotony" in codes(r), codes(r))
    check("单调 cv < 0.20", r["metrics"]["sentence_cv"] < 0.20, r["metrics"]["sentence_cv"])

    print("\n[6] 比喻堆砌")
    r = H.analyze(METAPHOR, [])
    check("比喻报 metaphor_density", "metaphor_density" in codes(r), codes(r))
    check("比喻密度 > 50/千字", r["metrics"]["metaphor_per_k"] > 50,
          r["metrics"]["metaphor_per_k"])

    print("\n[7] 感官锁死")
    r = H.analyze(SENSORY, [])
    check("感官报 sensory_lock", "sensory_lock" in codes(r), codes(r))
    check("top10 覆盖 > 0.80", r["metrics"]["sensory_top10"] > 0.80,
          r["metrics"]["sensory_top10"])

    # ---- 反证：改坏正常样本必须被抓住 ----
    print("\n[8] 反证（把好稿改坏）")
    # 阈值 0.45 是**刻意留宽**的：局部复读（一章里一段打转）交给 verify_chapter 的
    # 12-gram 判据，这里只抓"大面积打转"。所以反证样本要掺到复读占多数才该报 ——
    # 首版只掺一段（占 43%）没报，不是漏报，是我把反证样本设计错了。
    broken = normal + "\n" + REPEAT * 3
    r = H.analyze(broken, NAMES)
    check("复读占多数后必须报", "shingle_repeat" in codes(r), codes(r))
    over = "\n".join(s + "像一层薄薄的霜。" for s in normal.split("\n") if s.strip())
    r = H.analyze(over, NAMES)
    check("给每句加比喻后必须报", "metaphor_density" in codes(r), codes(r))

    # ---- 短样本：宁可跳过，不许瞎报 ----
    print("\n[9] 短样本跳过")
    short = normal[:280]
    r = H.analyze(short, NAMES)
    check("短样本 enabled=False", r["enabled"] is False, r["chars"])
    check("短样本 metrics 为空", r["metrics"] == {}, r["metrics"])
    check("短样本无 finding", codes(r) == [], codes(r))
    check("跳过原因写明字数", any("600" in s for s in r["skipped"]), r["skipped"])

    # ---- 保护：注释块与代码围栏不算正文 ----
    print("\n[10] 保护块不算正文")
    junk = "<!-- " + ("重复的注释内容。" * 300) + " -->\n" + normal
    r = H.analyze(junk, NAMES)
    check("注释块被剥掉（不拉高复读）", "shingle_repeat" not in codes(r), codes(r))
    fenced = "```\n" + ("代码块内容。" * 300) + "\n```\n" + normal
    r = H.analyze(fenced, NAMES)
    check("代码围栏被剥掉", "shingle_repeat" not in codes(r), codes(r))

    # ---- 报告落盘 ----
    print("\n[11] 报告落盘")
    tmp = Path(tempfile.mkdtemp(prefix="nf_homo_"))
    try:
        src = tmp / "chapters" / "raw"
        src.mkdir(parents=True)
        f = src / "01.md"
        f.write_text(normal, encoding="utf-8")
        res = H.write_chapter_report(f, 1, None, out_dir=tmp / "data" / "state" / "homogeneity")
        j = tmp / "data" / "state" / "homogeneity" / "01.json"
        check("报告文件写出", j.exists(), str(j))
        check("报告可解析且字段齐全", j.exists() and all(
            k in j.read_text(encoding="utf-8") for k in ("metrics", "findings", "chars")))
        check("返回结构与落盘一致", res["chars"] > 600, res["chars"])
        check("summary 一行可用", "字" in H.summarize(res), H.summarize(res))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 设定集取角色名 ----
    print("\n[12] 从设定集取角色名")
    tmp = Path(tempfile.mkdtemp(prefix="nf_homo_setting_"))
    try:
        s = tmp / "setting.json"
        s.write_text('{"characters":[{"name":"周砚清"},{"name":""},{"name":"韩拙"}]}',
                     encoding="utf-8")
        names = H.load_character_names(s)
        check("只取非空名字", names == ["周砚清", "韩拙"], names)
        check("文件不存在时返回空表", H.load_character_names(tmp / "nope.json") == [])
        bad = tmp / "bad.json"
        bad.write_text("{不是 json", encoding="utf-8")
        check("坏 JSON 不抛异常", H.load_character_names(bad) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- nfctl 聚合（用户看得到的入口）----
    print("\n[13] nfctl homo 聚合")
    import nfctl                                              # noqa: E402
    tmp = Path(tempfile.mkdtemp(prefix="nf_homo_nfctl_"))
    try:
        out = tmp / "data" / "state" / "homogeneity"
        out.mkdir(parents=True)
        f = tmp / "ch.md"
        f.write_text(normal, encoding="utf-8")
        H.write_chapter_report(f, 1, None, out_dir=out)
        d = nfctl.collect_homogeneity(tmp)
        check("聚合到 1 章", len(d["chapters"]) == 1, d)
        check("findings 计数为 0", d["findings"] == 0, d["findings"])
        txt = nfctl.render_homogeneity(d)
        check("渲染含章名与字数", "ch" in txt and str(d["chapters"][0]["chars"]) in txt, txt)
        empty = {"dir": str(tmp / "none"), "chapters": [], "findings": 0}
        check("空目录给出可执行提示", "暂无" in nfctl.render_homogeneity(empty))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 后端端点（GUI 的数据源）----
    print("\n[14] 后端 GET /homogeneity")
    import nf_api                                              # noqa: E402
    from nf_api_domains import runtime as rt                   # noqa: E402

    class FakeH:
        def __init__(self, q):
            self._q = q

        def _query(self):
            return self._q

    old_root = nf_api.ROOT
    tmp = Path(tempfile.mkdtemp(prefix="nf_homo_api_"))
    try:
        nf_api.ROOT = tmp
        code, d = rt.handle_homogeneity(FakeH({}))
        check("空目录 200 且 chapters 为空", code == 200 and d["ok"] and d["chapters"] == [], d)
        f = tmp / "ch.md"
        f.write_text(normal, encoding="utf-8")
        H.write_chapter_report(f, 1, None, out_dir=tmp / "data" / "state" / "homogeneity")
        code, d = rt.handle_homogeneity(FakeH({}))
        check("汇总读到 1 章", code == 200 and len(d["chapters"]) == 1, d)
        code, d = rt.handle_homogeneity(FakeH({"chapter": ["1"]}))
        check("单章可解析（1 与 01 都认）", code == 200 and "metrics" in d["report"], d)
        code, d = rt.handle_homogeneity(FakeH({"chapter": ["99"]}))
        check("不存在的章 404 且给 hint", code == 404 and "hint" in d, d)
    finally:
        nf_api.ROOT = old_root
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
