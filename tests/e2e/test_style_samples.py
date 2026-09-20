# -*- coding: utf-8 -*-
"""临时验证：子任务 B（范文片段注入） + 子任务 D（用户风格笔记）。

覆盖：
  B1 无范文 / 空范文 / 不可读路径 → ""（不注入）
  B2 有范文 → 输出含来源标注的小节
  B3 与当前正文高度相似的段落被剔除
  B4 build_polish_task / build_chapter_task 注入 style_samples
  D1 style_notes 为空 → ""；非空 → 用户笔记节
  D2 两个脚本注入 style_notes
"""
import os
import sys
import io
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

from utils.style_analyzer import (  # noqa: E402
    extract_style_samples, select_style_samples, build_style_notes_section,
)

FANWEN = """雨落了整整一夜。檐下的水线断了又续，续了又断，像谁在暗处一遍遍缝合着什么，却总也缝不上。

她坐在窗前，看着院子里那株老槐。树叶已经落尽了，枝桠黑瘦，像老人伸出的手，想要抓住些什么，终究什么也没抓住。不是她不想睡，而是睡不着；越是想睡，越是清醒。

老王猛地推开木门，吼道：你到底去不去？不去我可自己走了！
小李正在擦桌子，头也不抬：去哪儿？你说清楚。
去后山。听说那边有个洞，里头可能有东西。老王的嗓门又高了八度，一边说一边跺脚，鞋底上的泥扑簌簌往下掉。

短。

两人一前一后，踩着泥路往山上赶。天色渐渐暗下来，风也起来了。快到洞口时，老王忽然停住，压低声音说：你听——那是什么？
"""

fail = []


def check(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  " + extra) if extra else ""))
    if not cond:
        fail.append(name)


# ---------------------------------------------------------------- B1
print("=== B1 空值/异常路径 ===")
check("无路径 -> ''", extract_style_samples("") == "")
check("None -> ''", extract_style_samples(None) == "")
check("不存在的文件 -> ''", extract_style_samples("materials/__nope__.md") == "")

fd, empty_path = tempfile.mkstemp(suffix=".md")
os.close(fd)
check("空文件 -> ''", extract_style_samples(empty_path) == "")

# ---------------------------------------------------------------- B2
print("\n=== B2 正常范文 -> 含来源标注的小节 ===")
fd, fw_path = tempfile.mkstemp(suffix=".md", dir=ROOT)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(FANWEN)
rel = os.path.relpath(fw_path, ROOT).replace("\\", "/")

sec = extract_style_samples(rel, source=rel)
print(sec)
check("非空", bool(sec.strip()))
check("含小节标题", "范文片段" in sec)
check("含来源标注", "来源：" in sec and rel in sec)
check("含位置标注（第 N 段 / 字符区间）", "段，字符" in sec)
check("片段数 <= 3", sec.count("### 片段") <= 3)
check("短段（'短。'）未被选中", "\n短。\n" not in sec)

# ---------------------------------------------------------------- B3
print("\n=== B3 相似度过滤 ===")
picks_plain = select_style_samples(FANWEN)
labels_plain = [lb for lb, _ in picks_plain]
print("  无参照时选中:", labels_plain)

# 取第一个被选中的片段正文作为"当前正文"，它应被相似度过滤掉
dup = picks_plain[0][1]
picks_dup = select_style_samples(FANWEN, current_text=dup)
labels_dup = [lb for lb, _ in picks_dup]
print("  以其片段1为参照时选中:", labels_dup)
check("高度相似的段落被剔除", labels_plain[0] not in labels_dup)

picks_all = select_style_samples(FANWEN, current_text=FANWEN)
print("  以全文为参照时选中:", [lb for lb, _ in picks_all])
check("全文参照时全部剔除", len(picks_all) == 0)

# 段落切分：对白块不应被单换行切碎
from utils.style_analyzer import _split_paragraphs  # noqa: E402
paras = _split_paragraphs(FANWEN)
check("按空行分段（对白块保持完整）",
      any("老王猛地推开木门" in p and "去后山" in p for _, _, p in paras),
      "段数=%d" % len(paras))

# ---------------------------------------------------------------- D1
print("\n=== D1 用户风格笔记 ===")
for v in ("", "   ", None):
    check("空笔记 %r -> ''" % (v,), build_style_notes_section(v) == "")
notes = build_style_notes_section("多用短句，少用形容词。\n对话带点方言味。")
print(notes)
check("非空笔记含标题", "用户风格笔记" in notes)
check("非空笔记含原文", "方言味" in notes)
check("标注最高优先级", "最高优先级" in notes)

# ---------------------------------------------------------------- B4 / D2
print("\n=== B4/D2 阶段脚本注入 ===")
import yaml  # noqa: E402
from stage6_polish import build_polish_task  # noqa: E402
from stage4_writing import build_chapter_task  # noqa: E402

proj = yaml.safe_load(open("config/project.yaml", encoding="utf-8").read())
check("project.yaml 有 style_notes 字段", "style_notes" in proj.get("book", {}))

# 6: 无范文无笔记
body0 = build_polish_task({"book": {}}, "data/chapters/checked", "data/chapters/refined", [1])
check("stage6 无配置：style_samples 未注入", "范文片段" not in body0)
check("stage6 无配置：style_notes 未注入", "用户风格笔记" not in body0)
check("stage6 无配置：无残留占位符", "{{" not in body0)

# 6: 有范文 + 笔记
body1 = build_polish_task({"book": {"style_reference": rel, "style_notes": "多用短句"}},
                          "data/chapters/checked", "data/chapters/refined", [1])
check("stage6 有配置：注入范文片段", "范文片段" in body1)
check("stage6 有配置：注入用户笔记", "用户风格笔记" in body1 and "多用短句" in body1)
check("stage6 有配置：片段在风格指令之后",
      body1.index("风格模仿要求") < body1.index("范文片段") < body1.index("用户风格笔记"))
check("stage6 有配置：无残留占位符", "{{" not in body1)

# 4: 无配置 / 有配置
t0 = build_chapter_task({"chapter": {"target_words": [2000, 3000]}}, {"book": {}},
                        1, "data/outline/chapters/01.md", "data/setting/setting.json",
                        "data/summaries/rolling.md", [])
check("stage4 无配置：未注入范文片段/笔记",
      "范文片段" not in t0 and "用户风格笔记" not in t0)
t1 = build_chapter_task({"chapter": {"target_words": [2000, 3000]}},
                        {"book": {"style_reference": rel, "style_notes": "少用形容词"}},
                        1, "data/outline/chapters/01.md", "data/setting/setting.json",
                        "data/summaries/rolling.md", ["上一章末尾。"])
check("stage4 有配置：注入范文片段", "范文片段" in t1)
check("stage4 有配置：注入用户笔记", "用户风格笔记" in t1 and "少用形容词" in t1)
check("stage4 有配置：片段在风格参考之后",
      t1.index("## 风格参考") < t1.index("范文片段") < t1.index("用户风格笔记") < t1.index("## 写作要求"))
check("stage4 有配置：无残留占位符", "{{" not in t1)

print("\n" + ("全部通过" if not fail else "失败项: " + ", ".join(fail)))
sys.exit(1 if fail else 0)
