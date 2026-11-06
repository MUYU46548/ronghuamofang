# -*- coding: utf-8 -*-
"""字数口径：只计汉字（2026-11-05 用户拍板 Q-1/P2）。

## 锁什么

用户 2026-11-01 的硬样本：

    含标点 15 字，只计汉字 12 字 —— 差 19.6%（全书同比例）。

旧实现有三份 `count_cn_words`，其中写作端与判定端都是“去空白与 Markdown 符号后
计字符”，把标点、数字、拉丁字母全算成字 → 5000 的下限被标点灌水。

本轮定死：
1. **只计汉字**（唯一实现 `utils/word_count.py`）；
2. 三处旧函数必须是**同一个实现**（不许再长出第四份）；
3. 出厂区间按同等篇幅折算 `[4000, 7200]`，代码预设与 `config/system.yaml` 逐项一致；
4. 既有工作区的旧值 `[5000, 9000]` 有一次迁移，**只迁移恰好等于旧出厂值的**。

用法：python tests/unit/test_word_caliber.py
"""
import io
import json
import subprocess
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


SAMPLE = "“不达标”这个结论本身是假的。"
MIXED = "他说：ABC 123，——…！\n\n第二段。"


def t_count():
    print("\n【1】只计汉字")
    from utils.word_count import count_cn_words
    check("硬样本：含标点 15、汉字 12", len(SAMPLE) == 15 and count_cn_words(SAMPLE) == 12,
          (len(SAMPLE), count_cn_words(SAMPLE)))
    check("标点 / 数字 / 拉丁字母都不算",
          count_cn_words(MIXED) == len([c for c in MIXED if "\u4e00" <= c <= "\u9fff"]),
          count_cn_words(MIXED))
    check("空文本 = 0", count_cn_words("") == 0 and count_cn_words(None) == 0)


def t_single_impl():
    print("\n【2】三处调用点同一个实现（不许再长出第四份）")
    from utils.word_count import count_cn_words as canon
    import quality_checklist
    from utils.verify_chapter import count_cn_words as writer
    from utils.judge.text import count_cn_words as judge
    for name, fn in (("写作端 verify_chapter", writer), ("判定端 judge.text", judge),
                     ("checklist", quality_checklist.count_cn_words)):
        check("%s 与唯一实现读数一致" % name, fn(MIXED) == canon(MIXED),
              (fn(MIXED), canon(MIXED)))
    # 源码里不许再出现“计字符”的旧写法
    for rel in ("scripts/utils/verify_chapter.py", "scripts/utils/judge/text.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        check("%s 不再自带字符计数实现" % rel,
              "re.sub(r\"[#*`>_|" not in src and "word_count" in src)


def t_preset():
    print("\n【3】出厂区间 = [4000, 7200]（代码与配置逐项一致）")
    from utils.config_io import load_config_yaml
    from nf_api_domains.project import TARGET_WORDS_PRESET
    cfg = load_config_yaml(ROOT / "config" / "system.yaml")
    tw = (cfg.get("chapter") or {}).get("target_words")
    check("config/system.yaml = [4000, 7200]", tw == [4000, 7200], tw)
    check("TARGET_WORDS_PRESET 与配置一致", list(TARGET_WORDS_PRESET) == tw,
          TARGET_WORDS_PRESET)
    check("注释写明只计汉字口径",
          "只计汉字" in (ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))


def t_migration():
    print("\n【4】旧工作区迁移：只动恰好等于旧出厂值的")
    script = ROOT / "scripts" / "migrate_word_caliber.py"
    for old, should in (([5000, 9000], True), ([4500, 8000], False)):
        tmp = Path(tempfile.mkdtemp(prefix="nf_cal_"))
        try:
            cfg = tmp / "config" / "system.yaml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("engine: self\nchapter:\n  target_words: %s\n  summary_every: 15\n"
                           % json.dumps(old), encoding="utf-8")
            before = cfg.read_text(encoding="utf-8")
            # 预演：不写
            subprocess.run([sys.executable, str(script), "--root", str(tmp)],
                           capture_output=True, text=True, encoding="utf-8")
            check("预演不改文件（%s）" % old, cfg.read_text(encoding="utf-8") == before)
            p = subprocess.run([sys.executable, str(script), "--root", str(tmp), "--yes"],
                               capture_output=True, text=True, encoding="utf-8")
            text = cfg.read_text(encoding="utf-8")
            if should:
                check("旧值 5000/9000 → 4000/7200", "target_words: [4000, 7200]" in text,
                      p.stdout[-160:])
                check("邻近键未被误删", "summary_every: 15" in text)
                check("写前有备份", any((tmp / "config" / "history").glob("*.yaml")))
            else:
                check("用户自定义值不动（4500/8000）", "target_words: [4500, 8000]" in text,
                      p.stdout[-160:])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


def t_prompts():
    """提示词里的字数口径必须与判定同源（2026-11-06，A3 字数达标根因）。

    病：判定只计汉字，而 stage4 的任务模板写的是“字数 4000-7200 字” ——
    模型按含标点理解，写满 4000 字只有 ~3100 汉字，天生差四分之一，
    全靠打回/扩写硬补；边界章就停在 3847~4512 那种“补了一半”的位置。
    """
    print("\n【5】写作提示词必须说“汉字”，并给余量")
    root = Path(__file__).resolve().parents[2]
    for rel in ("prompts/stage4_writing.md", "prompts/stage4_group.md",
                "prompts/stage4_expand.md"):
        t = (root / rel).read_text(encoding="utf-8")
        check("%s 说清只数汉字" % rel, "汉字" in t)
        check("%s 没有再写裸的“字数 X 字”" % rel,
              "字数 {{min_words}}-{{max_words}} 字" not in t
              and "{{quota_low}}-{{quota_high}} 字" not in t)

    # 真渲染一次扩写任务：目标值/需净增数必须是算出来的数，不是占位符
    from stage4_writing import build_expand_task
    body = build_expand_task({"chapter": {"target_words": [4000, 7200]}},
                             {"book": {"name": "夹具"}}, 3, 3200, 4000, 7200)
    check("扩写任务含“至少净增 N 个汉字”", "至少" in body and "净增" in body, body[:120])
    check("扩写任务的目标值已渲染（不留 {{}}）", "{{" not in body)
    check("扩写任务给出 4200 目标（4000×1.05）", "4200" in body,
          [ln for ln in body.splitlines() if "4200" in ln][:1])


def main():
    t_count()
    t_single_impl()
    t_preset()
    t_migration()
    t_prompts()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
