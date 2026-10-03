# -*- coding: utf-8 -*-
"""文风特征 v2 自检（边界情况 + 输出丰富度 + 区分度），零 LLM。

## 2026-10-03 修：它以前是**纯 print 的临时脚本**（且被 AGENTS.md 当自检宣传）

```python
print("旧键齐全:", all(k in f for k in OLD_KEYS))
```
`False` 也照样退 0 → `nfctl test` 记成通过。**"只会展示、不会判定"的用例比没有更糟**：
它让人以为这条路径被验过了。现改成断言 + 非零退出，并保留原有的三个观察维度：
① 边界输入不炸（None/空/空白/极短/无标点/纯标点）；② 输出丰富度（v2 新键确实存在）；
③ 区分度（抒情段 vs 口语段的特征与据此生成的风格指令必须不同）。

用法：python tests/unit/test_style_v2.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from utils.style_analyzer import build_style_instruction, extract_style_features  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


OLD_KEYS = ["avg_sentence_len", "short_sentence_ratio", "long_sentence_ratio",
            "common_phrases", "metaphor_density", "personification_density",
            "dialogue_ratio", "avg_paragraph_len"]

LYRIC = """
雨落了整整一夜。檐下的水线断了又续，续了又断，像谁在暗处一遍遍缝合着什么，却总也缝不上。
她坐在窗前，看着院子里那株老槐。树叶已经落尽了，枝桠黑瘦，像老人伸出的手，想要抓住些什么，终究什么也没抓住。
不是她不想睡，而是睡不着。越是想睡，越是清醒；越是清醒，越是听见雨声里有什么东西在缓慢地碎裂。
她想起那年春天，他站在槐树下，笑着说：等秋天来了，我们就走。
后来秋天来了很多次。他没有再来。
风忽然大了。窗纸簌簌地响。她伸手，摸到一片凉。
泪是热的。
"""

COLLOQUIAL = """
老王猛地推开木门，吼道：“你到底去不去？不去我可自己走了！”
小李正在擦桌子，头也不抬：“去哪儿？你说清楚。”
“去后山。听说那边有个洞，里头可能有东西。”老王的嗓门又高了八度，一边说一边跺脚，鞋底上的泥扑簌簌往下掉。
小李这才放下抹布。他其实不想去，可是又怕老王一个人出事。于是他抓起手电筒，跟着往外走。
两人一前一后，踩着泥路往山上赶。天色渐渐暗下来，风也起来了。
快到洞口时，老王忽然停住。“你听。”他说。
"""


def case_boundaries():
    print("\n【1】边界输入：不炸、不硬凑特征（空/极短/无标点/纯标点）")
    for raw in (None, "", "   ", "短", "啊" * 99):
        try:
            r = extract_style_features(raw)
            ok = (r is None) or (isinstance(r, dict) and not r)
        except Exception as e:                              # noqa: BLE001
            r, ok = "%s: %s" % (type(e).__name__, e), False
        check("extract_style_features(%r) → 空结果而非硬凑" % (repr(raw)[:14],), ok, r)

    for raw in ("啊" * 100, "abc" * 40, "，。！？；…" * 30,
                "这是一段没有任何标点的超长中文文本用来测试分词与统计的健壮性" * 4):
        try:
            r = extract_style_features(raw)
            ok = isinstance(r, dict) and bool(r)
        except Exception as e:                              # noqa: BLE001
            r, ok = "%s: %s" % (type(e).__name__, e), False
        check("足够长的输入（%d 字符）→ 给出特征" % len(raw), ok, r if not ok else "")

    check("build_style_instruction({}) 返回字符串（不是抛异常）",
          isinstance(build_style_instruction({}), str))
    check("build_style_instruction(None) 返回字符串",
          isinstance(build_style_instruction(None), str))


def case_richness_and_discrimination():
    print("\n【2】输出丰富度 + 区分度（两类明显不同的文本必须给出不同结论）")
    fa, fb = extract_style_features(LYRIC), extract_style_features(COLLOQUIAL)
    check("抒情段与口语段都拿到特征", bool(fa) and bool(fb))
    check("旧键全部保留（向后兼容：下游按这些键取值）",
          all(k in fa for k in OLD_KEYS) and all(k in fb for k in OLD_KEYS),
          sorted(set(OLD_KEYS) - set(fa)))
    check("v2 确有新增键（丰富度）", len(fa) > len(OLD_KEYS) and len(fb) > len(OLD_KEYS),
          (len(fa), len(fb), len(OLD_KEYS)))
    check("两段文本的特征不完全相同（区分度）", fa != fb)
    diff = sorted(k for k in fa if fa.get(k) != fb.get(k))
    check("差异维度 ≥ 3 个（不是只有一个键碰巧不同）", len(diff) >= 3, diff)
    check("对白比例把口语段与抒情段分开",
          fb.get("dialogue_ratio", 0) > fa.get("dialogue_ratio", 0),
          (fa.get("dialogue_ratio"), fb.get("dialogue_ratio")))
    ia, ib = build_style_instruction(fa), build_style_instruction(fb)
    check("两段文本生成的风格指令非空", bool(ia.strip()) and bool(ib.strip()),
          (len(ia), len(ib)))
    check("风格指令本身也不同（否则模型收到的指引是一样的）", ia != ib)


def main():
    print("=" * 62)
    print("  文风特征 v2 自检")
    print("=" * 62)
    case_boundaries()
    case_richness_and_discrimination()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
