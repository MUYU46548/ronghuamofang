# -*- coding: utf-8 -*-
"""分支样例锚点（第 4 件 · 选项可视化）的回归锁。

背景（用户原话）：「竞品有选项可视化，分支样例方向直观可选，显然根本没做进
绒花墨坊里，这条信息又在某次会话中丢失了。如果我这里不提就永远没了。」

此前 stage4 写作只吃 5 个骨架字段，样例文没有安放位置。现在讨论协议的锚点机制
（<!--opt-anchor -->）接到 stage4：用户在章大纲里选定「样例文本」→ stage4
生成任务时读出来 → 注入成 few-shot 锚点。

本文件锁五件事：

1. **只注入样例类锚点**：kind=sample 的进，kind=discussion / 字段改写的不进
   —— 后两者是给**人**看的决策记录，混进 few-shot 会污染写作模型。
2. **锚点元数据不外漏**：注入块里不得出现 opt-anchor 注释或 JSON 字段
   （那会把「为什么要改」当成「照这个写」的指令喂给模型）。
3. **全量注入不截断**（用户口径：整套写作用不了多少 token，样例文是精心磨出来
   的，截断可能丢掉关键语气/节奏信息），唯一上界是单块 4000 字且截断必标注。
4. **空章/不存在章兜底**：返回空串，不抛异常（写作流程不能被样例读取拖死）。
5. **模板渲染无残留**：{{chapter_samples}} 真被替换；不传变量时保留占位符
   （让 template_loader 的残留告警可见，而不是静默漏给模型）。

用法：python tests/unit/test_chapter_samples.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import chapter_samples                       # noqa: E402
from utils.template_loader import load_template         # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


CHAP = """## 第三章 仙台争花火

- 核心事件：
  1. 主角甲与主角乙争论出征规模
- 涉及角色：主角甲；主角乙
- 功能：转折
- 衔接：承接第二章；钩子——远征队信号

## 【优化点1】说服段的写法
- 理由：说服段缺可感知的代价
- 样例文（对话）：
  > 「我想起故人终战那次……如果我们当时有一支舰队……」

<!--opt-anchor {"id":"ch3-opt1","kind":"sample","tags":["说服","旧地线"],"weight":"high"} -->

## 【优化点2】这轮讨论的方向
- 理由：先确认基调再动笔

<!--opt-anchor {"id":"ch3-opt2","kind":"discussion","rounds":3} -->

## 【优化点3】叙事要点改写
- 理由：原要点太空泛

<!--opt-anchor {"id":"ch3-opt3","kind":"blueprint_patch","field":"events"} -->
"""


def _tmp_root(chap_text=CHAP):
    t = Path(tempfile.mkdtemp(prefix="chap_samples_"))
    (t / "data" / "outline" / "chapters").mkdir(parents=True)
    (t / "data" / "outline" / "chapters" / "03.md").write_text(
        chap_text, encoding="utf-8")
    return t


# ------------------------------------------------------- 1. 样例注入
def test_sample_injection():
    print("\n[1] 样例类锚点注入")
    t = _tmp_root()
    try:
        out = chapter_samples.chapter_samples(3, root=str(t))
        check("注入块头出现", "本章样例文本" in out)
        check("样例正文进来", "故人终战" in out)
        check("标题行进来", "说服段的写法" in out)
    finally:
        shutil.rmtree(t)


# ------------------------------------------------------- 2. 锚点隔离
def test_anchor_isolation():
    print("\n[2] 非样例类锚点不注入 + 元数据不外漏")
    t = _tmp_root()
    try:
        out = chapter_samples.chapter_samples(3, root=str(t))
        check("讨论类锚点不注入", "这轮讨论的方向" not in out)
        check("字段改写类锚点不注入", "叙事要点改写" not in out)
        check("锚点注释不外漏", "opt-anchor" not in out)
        check("JSON 字段不外漏", "discussion" not in out and "blueprint_patch" not in out)
        check("字段名不外漏", '"field"' not in out)
    finally:
        shutil.rmtree(t)


# ------------------------------------------------------- 3. 全量注入
def test_no_topk_truncation():
    print("\n[3] 全量注入（不做 top-K 截断）")
    # 造 5 块样例，每块 800 字 —— 远超任何 top-K 阈值，但都在 4000 字上界内
    blocks = []
    for i in range(1, 6):
        body = f"样例{i}：" + ("这是一段用于测试的样例文本。" * 40)
        blocks.append(
            f"## 【优化点{i}】样例{i}\n- 理由：测试\n\n{body}\n\n"
            f'<!--opt-anchor {{"id":"s{i}","kind":"sample"}} -->\n')
    t = _tmp_root(chap_text="## 第三章\n\n- 核心事件：\n  1. x\n- 涉及角色：甲\n\n"
                            + "\n".join(blocks))
    try:
        out = chapter_samples.chapter_samples(3, root=str(t))
        for i in range(1, 6):
            check(f"样例{i} 全量注入（不被 top-K 砍）", f"样例{i}：" in out)
        check("无截断标注（都没超 4000 字上界）", "超长已截断" not in out)
    finally:
        shutil.rmtree(t)


# ------------------------------------------------------- 4. 超长块截断
def test_long_block_clip():
    print("\n[4] 单块超 4000 字截断并标注")
    huge = "超长样例。" * 1000          # 约 5000 字
    t = _tmp_root(chap_text="## 第三章\n\n- 核心事件：\n  1. x\n- 涉及角色：甲\n\n"
                            f"## 【优化点1】超长\n\n{huge}\n\n"
                            '<!--opt-anchor {"id":"s1","kind":"sample"} -->\n')
    try:
        out = chapter_samples.chapter_samples(3, root=str(t))
        check("截断标注出现（不静默）", "超长已截断" in out)
        check("标注含原字数", "原" in out and "字" in out)
    finally:
        shutil.rmtree(t)


# ------------------------------------------------------- 5. 空章兜底
def test_fallbacks():
    print("\n[5] 空章 / 不存在章 / 无样例章兜底")
    t = _tmp_root(chap_text="## 第三章\n\n- 核心事件：\n  1. x\n- 涉及角色：甲\n")
    try:
        check("无样例章返回空串", chapter_samples.chapter_samples(3, root=str(t)) == "")
        check("不存在章返回空串", chapter_samples.chapter_samples(99, root=str(t)) == "")
        check("章号 0 返回空串", chapter_samples.chapter_samples(0, root=str(t)) == "")
    finally:
        shutil.rmtree(t)


# ------------------------------------------------------- 6. 模板渲染
def test_template_render():
    print("\n[6] 模板渲染无残留")
    t = _tmp_root()
    try:
        block = chapter_samples.chapter_samples(3, root=str(t))
        _, body = load_template("stage4_writing.md", {
            "n": 3, "book_name": "测试书",
            "path_outline": "x", "path_setting": "x", "path_rolling": "x",
            "path_project": "x", "prev_tail": "上一章末尾",
            "style_reference": "x", "style_samples": "风格样例",
            "style_notes": "", "kb_context": "kb", "character_cards": "角色卡",
            "min_words": 2000, "max_words": 3000, "path_output": "out.md",
            "chapter_samples": block,
        })
        check("无占位符残留", "{{chapter_samples}}" not in body)
        check("样例正文进渲染结果", "故人终战" in body)
        i_style = body.find("风格样例")
        i_ch = body.find("故人终战")
        i_kb = body.find("kb")
        check("注入顺序 style < samples < kb",
              -1 not in (i_style, i_ch, i_kb) and i_style < i_ch < i_kb,
              (i_style, i_ch, i_kb))

        # 反证：不传变量时保留占位符（好让 template_loader 的残留告警可见）
        _, body2 = load_template("stage4_writing.md", {
            "n": 3, "book_name": "测试书",
            "path_outline": "x", "path_setting": "x", "path_rolling": "x",
            "path_project": "x", "prev_tail": "上一章末尾",
            "style_reference": "x", "style_samples": "风格样例",
            "style_notes": "", "kb_context": "kb", "character_cards": "角色卡",
            "min_words": 2000, "max_words": 3000, "path_output": "out.md",
        })
        check("不传变量时保留占位符（触发残留告警）",
              "{{chapter_samples}}" in body2)
    finally:
        shutil.rmtree(t)


def test_real_write_path():
    """走真实写回路径（append_opt_point）产出的块必须能被读回。

    2026-10-18 实测 bug：读取端只认**单行**标题（`^## 【优化点N】标题$`），而
    append_opt_point 把整段样例文塞进标题行之后 —— 多行样例在单行正则下整个块
    被漏掉，few-shot 静默失效（GUI 批准方向样例后 stage4 读不到，无任何报错）。
    此前 23 个用例全用手写固定文本，从不走写回路径，所以全绿却漏了这个 bug。
    """
    print("\n[7] 真实写回路径（append_opt_point → chapter_samples）")
    from utils.chapter_blueprint import append_opt_point

    base = "## 第三章\n\n- 核心事件：\n  1. x\n- 涉及角色：甲\n"
    t = Path(tempfile.mkdtemp(prefix="real_write_"))
    try:
        (t / "data" / "outline" / "chapters").mkdir(parents=True)

        # 多行样例（真实样例文必然多行）
        multi = append_opt_point(
            base, "第一行。\n\n第二行。\n\n> 引用一句对话。",
            {"kind": "sample", "target_n": 3, "label": "克制"})
        (t / "data" / "outline" / "chapters" / "03.md").write_text(
            multi, encoding="utf-8")
        out = chapter_samples.chapter_samples(3, root=str(t))
        check("多行样例：首行不丢（标题行里那截并回正文）", "第一行。" in out)
        check("多行样例：后续行进来", "第二行。" in out)
        check("多行样例：引用块进来", "引用一句对话" in out)
        check("多行样例：标题取锚点 label", "克制" in out)

        # 标题行嵌正文（append_opt_point 的旧产物格式）
        inline = ("## 第三章\n\n- 核心事件：\n  1. x\n\n"
                  "## 【优化点1】主角甲没有立刻回答。他把星图推回去。\n\n"
                  '<!--opt-anchor {"kind": "sample", "target_n": 3, "n": 1} -->\n')
        (t / "data" / "outline" / "chapters" / "04.md").write_text(
            inline, encoding="utf-8")
        out2 = chapter_samples.chapter_samples(4, root=str(t))
        check("标题行嵌正文：正文不丢", "主角甲没有立刻回答" in out2)
        check("标题行嵌正文：回退编号标题", "优化点" in out2)

        # 非样例锚点走同一路径仍不注入（回归）
        other = append_opt_point(
            base, "这是给人看的讨论意见。",
            {"kind": "discussion", "target_n": 5})
        (t / "data" / "outline" / "chapters" / "05.md").write_text(
            other, encoding="utf-8")
        check("非样例锚点（真实写回）仍不注入",
              chapter_samples.chapter_samples(5, root=str(t)) == "")
    finally:
        shutil.rmtree(t)


def main():
    test_sample_injection()
    test_anchor_isolation()
    test_no_topk_truncation()
    test_long_block_clip()
    test_fallbacks()
    test_template_render()
    test_real_write_path()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
