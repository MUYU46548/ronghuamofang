# -*- coding: utf-8 -*-
"""临时验证脚本：style_analyzer v2 边界情况 + 输出丰富度/区分度对比。"""
import sys, io, json
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from utils.style_analyzer import extract_style_features, build_style_instruction

OLD_KEYS = ["avg_sentence_len", "short_sentence_ratio", "long_sentence_ratio",
            "common_phrases", "metaphor_density", "personification_density",
            "dialogue_ratio", "avg_paragraph_len"]

# ---------- 1. 边界情况 ----------
cases = [None, "", "   ", "短", "啊" * 99, "啊" * 100, "abc" * 40, "，。！？；…" * 30]
print("=== 边界情况 ===")
for c in cases:
    r = extract_style_features(c)
    label = repr(c)[:20]
    if r:
        print(f"{label:24s} -> dict(keys={len(r)}) avg={r.get('avg_sentence_len')}")
    else:
        print(f"{label:24s} -> {{}}  指令长度={len(build_style_instruction(r))}")
print("build_style_instruction({}) =", repr(build_style_instruction({})))
print("build_style_instruction(None) =", repr(build_style_instruction(None)))

# 纯中文长文本但无标点
weird = "这是一段没有任何标点的超长中文文本用来测试分词与统计的健壮性" * 4
print("无标点长文本 ->", len(extract_style_features(weird)), "keys")

# ---------- 2. 两种文风对比 ----------
A = """
雨落了整整一夜。檐下的水线断了又续，续了又断，像谁在暗处一遍遍缝合着什么，却总也缝不上。
她坐在窗前，看着院子里那株老槐。树叶已经落尽了，枝桠黑瘦，像老人伸出的手，想要抓住些什么，终究什么也没抓住。
不是她不想睡，而是睡不着。越是想睡，越是清醒；越是清醒，越是听见雨声里有什么东西在缓慢地碎裂。
她想起那年春天，他站在槐树下，笑着说：等秋天来了，我们就走。
后来秋天来了很多次。他没有再来。
风忽然大了。窗纸簌簌地响。她伸手，摸到一片凉。
泪是热的。
"""

B = """
老王猛地推开木门，吼道：你到底去不去？不去我可自己走了！
小李正在擦桌子，头也不抬：去哪儿？你说清楚。
去后山。听说那边有个洞，里头可能有东西。老王的嗓门又高了八度，一边说一边跺脚，鞋底上的泥扑簌簌往下掉。
小李这才放下抹布。他其实不想去，可是又怕老王一个人出事。于是他抓起手电筒，跟着往外走。
两人一前一后，踩着泥路往山上赶。天色渐渐暗下来，风也起来了。
快到洞口时，老王忽然停住。你听，他说。
"""

for name, txt in (("A 抒情沉郁", A), ("B 口语动作", B)):
    f = extract_style_features(txt)
    print("\n" + "=" * 70)
    print("样本:", name, " 文本长度:", f.get("text_length"))
    print("旧键齐全:", all(k in f for k in OLD_KEYS))
    print("新增键数:", len(f) - len(OLD_KEYS))
    print(json.dumps({k: v for k, v in f.items() if k not in OLD_KEYS},
                     ensure_ascii=False, indent=1)[:1600])

print("\n" + "=" * 70)
print("【A 的风格指令】")
print(build_style_instruction(extract_style_features(A)))
print("\n" + "=" * 70)
print("【B 的风格指令】")
print(build_style_instruction(extract_style_features(B)))
