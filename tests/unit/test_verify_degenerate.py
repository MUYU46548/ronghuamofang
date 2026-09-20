# -*- coding: utf-8 -*-
"""退化检测自检（审计行动项 10）：stage4/6 正文非空 + 非退化硬断言。

## 为什么需要这套用例

`check_chapter` 原有的判据（标题/字数/占位符/复杂元素）只能抓「产出太空」。
**产出够长却全是垃圾时，原判据全部漏过** —— 本次改造前实测（把退化样本
字数调进 `[2000,3000]` 区间后跑旧 `check_chapter`）：

    样本          字数   旧判据
    复读填充      2513  PASS(漏)
    思考残片      2513  PASS(漏)
    元话语拒答    2218  PASS(漏)
    无段落换行    2432  PASS(漏)
    标点灌水      2513  PASS(漏)
    模板骨架      2513  PASS(漏)

后果是**静默失败**：垃圾正文一路流到 stage6 润色与交付 Word。

## 测试设计三条纪律（本项目血泪积累）

1. **反向验证是唯一可信的绿灯判据**：每条判据都必须有「删掉它 → 对应样本
   必须变绿」的反证。没有反证的用例很可能是永远为绿的空断言。
2. **判据隔离**：每个退化样本只带**一种**靶向特征，其余内容用**互不重复的
   正常文学句**填充。否则 n-gram 判据会替所有样本"抢答"击中，
   掩盖真实缺口（本次开发中就踩过这个坑）。
3. **真实产物零误报**：用仓库内真实章节文件做负例。判据不能把
   「对话密集、单句成段」的中文小说标准写法当退化 —— 这是第一版
   平均段长判据踩过的坑（7/19 命中，5 个误报，均长 26.3~29.1 全是健康章）。
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from utils.verify_chapter import (  # noqa: E402
    MAX_NGRAM_REPEAT_RATIO, MAX_SINGLE_PARA_RATIO, MIN_PARAGRAPHS,
    MIN_RATIO_BODY_CHARS, ChapterCheck, check_chapter, check_degenerate,
    is_chapter_complete,
)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


# ---------------------------------------------------------------- 测试素材

TITLE = "## 第9章 测试章\n\n"

# 互不重复的正常文学句：用来把样本撑到有效体量，
# 同时**不引入** n-gram 重复等旁路特征。
_POOL = [
    "她推开门，风从走廊尽头卷过来，带着旧纸和雨水的气味。",
    "桌上的茶已经凉透了，杯壁凝着一圈深褐色的渍。",
    "他数了数口袋里的铜板，只够买半个馕和一夜的床位。",
    "远处传来钟声，一下，两下，第三下被风吞掉了。",
    "明璋把地图摊在膝上，指尖沿着山脉的走向慢慢滑过去。",
    "雨点打在铁皮棚顶上，密集得像有人在外头撒豆子。",
    "她没有回头，只是把围巾往上拉了拉，遮住半张脸。",
    "马车驶过石桥时颠了一下，盒子里的瓷片轻轻磕碰。",
    "烛火晃了两下，墙上的人影跟着拉长又缩回去。",
    "老头眯起眼睛，把烟杆在鞋底上磕了磕，火星掉进泥里。",
    "河面上浮着一层薄雾，对岸的灯火看起来远得要命。",
    "他把信纸折了三折，塞进贴身的口袋，指尖有点抖。",
    "孩子在巷口追着一只纸鸢跑，笑声碎在屋檐底下。",
    "铁匠铺的炉子还红着，锤声一下一下敲在寂静的午后。",
]


def _filler(min_chars):
    """生成 ≥min_chars 字、段长自然波动的**不重复**填充。"""
    out, total = [], 0
    for i in range(120):
        s = _POOL[i % len(_POOL)]
        if i >= len(_POOL):
            s = s.rstrip("。") + "，" + _POOL[(i * 7 + 3) % len(_POOL)]
        out.append(s)
        total += len(s)
        if total >= min_chars:
            break
    return "\n\n".join(out)


def sample(target_lines, pad_to=2200):
    """靶向段落 + 不重复填充，组成字数落在 [2000,3000] 的样本。"""
    body = "\n\n".join(target_lines)
    if len(body) < pad_to:
        body += "\n\n" + _filler(pad_to - len(body))
    return TITLE + body + "\n\n<!-- quality: 7/10 -->\n"


# 六种退化形态（**判据隔离**：每样本只带一种靶向特征）
DEGEN_SAMPLES = {
    # n-gram 靶向：同一句重复 40 遍
    "复读填充": ["林墨走进茶馆，看见桌上放着一封信，他伸手拿起来拆开。"] * 40,
    # reasoning 靶向：模型自述规划语言，每句不同（避免 n-gram 抢答）
    "思考残片": [
        "让我想想这一章的节奏该怎么安排才好。",
        "首先我需要确认暮雨现在所处的位置。",
        "接下来要考虑对话部分是否需要压缩。",
        "我应该先写场景，然后再处理人物冲突。",
        "按照大纲，我需要注意字数的控制范围。",
        "考虑到篇幅，我需要把次要线索合并。",
        "用户希望看到更激烈的对抗场面。",
        "这一步需要谨慎处理伏笔的埋设位置。",
        "这一章大概需要三千字左右的篇幅。",
        "我可以在结尾处留一个悬念给下章。",
        "让我分析一下人物的动机是否合理。",
        "然后我要检查前后文的连贯性问题。",
    ] * 4,
    # meta 靶向：拒答/元话语，每句不同
    "元话语拒答": [
        "作为一个AI助手，我无法完成这个章节的写作任务。",
        "很抱歉，我不能提供涉及暴力的情节内容。",
        "如果您需要，我可以改为提供一份大纲建议。",
        "以下是您需要的内容的说明文档，请自行补充。",
        "作为语言模型，我需要提醒本章内容需要作者自行撰写。",
        "我不能代替作者做出关于人物命运的决定。",
        "很抱歉，这个请求超出了我的能力范围。",
        "如果您希望，我可以提供一个故事的梗概。",
    ] * 4,
    # 段落结构靶向：整章无换行（必须单独构造，不能填充）
    "无段落换行": None,
    # content_ratio 靶向：纯标点（**不能填充** —— 填充会稀释 content_ratio，
    # 使样本变成「70% 正常文字 + 30% 标点」，那按定义就不算标点灌水）。
    # 规模须 ≥ 真实章节下限（2000 字），否则会被 MIN_RATIO_BODY_CHARS 门槛
    # 整体跳过 —— 开发时用 480 字的样本踩过这个坑，得到"判据失灵"的假象。
    "标点灌水": ["。" * 400, "，" * 400, "！" * 400, "？" * 400, "；" * 400, "、" * 400],
    # skeleton 靶向：未填充占位
    "模板骨架": [
        "【场景描写】：咖啡馆内部，昏黄灯光从吊灯上洒下来。",
        "{角色A}推门走进来，{角色B}抬起头看他。",
        "[对话内容]：此处填写两人之间的第一轮交锋对话。",
        "<情节推进>：冲突在这里爆发，主角发现秘密。",
        "【情绪基调】：压抑中带着一丝不易察觉的温情。",
        "【人物动作】：{角色C}缓慢地坐下，手指敲了三下。",
        "[环境音效]：远处传来钟声，混着街上的马车声。",
        "<转折点>：{角色A}从怀里掏出了那封信。",
        "【心理描写】：她心里在盘算，嘴上却什么都没说。",
        "{角色D}站起来走到窗边，看着外面的雨。",
        "[旁白]：这一刻，两个人都明白事情无法回头。",
        "<伏笔>：桌上那盏灯的火苗，忽然晃了一下。",
    ] * 4,
}

# 每种形态**应该**触发哪个判据关键词（用于验证是靶向判据命中，而非旁路抢答）
EXPECT_KEYWORD = {
    "复读填充": "复读",
    "思考残片": "思考残片",
    "元话语拒答": "元话语",
    "无段落换行": "段落过少",
    "标点灌水": "标点灌水",
    "模板骨架": "模板骨架",
}


def _make_sample(name, tmpdir):
    p = Path(tmpdir) / f"{name}.md"
    if DEGEN_SAMPLES[name] is None:
        txt = (TITLE
               + ("他沿着街道一直往前走，路灯一盏一盏亮起来，"
                  "行人在他身边经过，没有人回头看他一眼。" * 55)
               + "\n\n<!-- quality: 7/10 -->\n")
    elif name == "标点灌水":
        # 纯标点，不填充：确保 content_ratio 直接反映「标点灌水」这一特征。
        # 同时用不同标点分组，避免全靠 n-gram 重复率命中。
        txt = TITLE + "\n\n".join(DEGEN_SAMPLES[name]) + "\n\n<!-- quality: 7/10 -->\n"
    else:
        txt = sample(DEGEN_SAMPLES[name])
    p.write_text(txt, encoding="utf-8")
    return p


# 真实产物路径（负例：必须零误报）
_REAL_DIRS = [
    "data/books/测试用例废稿/chapters/checked",
    "data/books/测试用例废稿/chapters/raw",
    "data/books/测试用例废稿/chapters/refined",
]
# 仓库里已知的两份**真**退化遗留（62 字截断 / 500 字无换行），不算误报
_KNOWN_BAD = {"04.md", "01.md"}


def _real_chapters():
    root = Path(__file__).resolve().parents[2]
    out = []
    for d in _REAL_DIRS:
        dd = root / d
        if dd.is_dir():
            out += sorted(dd.glob("*.md"))
    return out


def main():
    tmpdir = tempfile.mkdtemp(prefix="degen_")
    try:
        # ============ A. 六形态检出（含靶向判据归属） ============
        print("\n=== A. 六种退化形态检出 ===")
        for name in DEGEN_SAMPLES:
            p = _make_sample(name, tmpdir)
            r = check_chapter(p, 2000, 3000)
            check(f"A1 {name} 检出退化", bool(r.degenerate),
                  f"→ degenerate={r.degenerate}")
            kw = EXPECT_KEYWORD[name]
            hit = any(kw in d for d in r.degenerate)
            check(f"A2 {name} 由靶向判据（{kw}）命中", hit,
                  f"→ {r.degenerate}")
            check(f"A3 {name} 字数在区间内（排除字数判据干扰）",
                  2000 <= r.word_count <= 3000, f"→ {r.word_count}")
            check(f"A4 {name} 计入 result.errors 且 ok=False",
                  (not r.ok) and any(d.startswith("退化：") for d in r.errors),
                  f"→ ok={r.ok} errors={r.errors}")

        # ============ B. 真实产物零误报 ============
        print("\n=== B. 真实章节零误报（负例） ===")
        real = _real_chapters()
        if not real:
            print("  [SKIP] 未找到真实章节文件（可能是干净检出）")
        else:
            false_pos = []
            for f in real:
                r = check_chapter(f, 500, 6000)
                if r.degenerate and f.name not in _KNOWN_BAD:
                    false_pos.append((f.name, r.degenerate))
                    # 只在 checked/refined 目录找，raw 里有历史遗留
            check("B1 真实健康章节零误报", not false_pos,
                  f"→ 误报: {false_pos}")
            # 真实正样本：checked/01.md 必须有健康指标
            good = [f for f in real if f.name == "01.md" and "checked" in str(f)]
            if good:
                r = check_chapter(good[0], 2000, 3000)
                m = r.degen_metrics
                check("B2 真实正样本不被判退化", not r.degenerate,
                      f"→ {r.degenerate}")
                check("B2b 真实正样本 n-gram 重复率极低",
                      m.get("ngram_repeat", 1) < 0.05,
                      f"→ {m.get('ngram_repeat')}")
                check("B2c 真实正样本 content_ratio 健康",
                      m.get("content_ratio", 0) > 0.7,
                      f"→ {m.get('content_ratio')}")
                check("B2d 真实正样本单段占比远低于阈值",
                      m.get("longest_para", 9e9) / max(m.get("body_chars", 1), 1)
                      < MAX_SINGLE_PARA_RATIO,
                      f"→ {m.get('longest_para')}/{m.get('body_chars')}")

        # ============ C. 判据隔离：每条判据独立有效 ============
        print("\n=== C. 判据隔离（禁用他判据后仍能命中） ===")
        import utils.verify_chapter as vc

        # C1 标点灌水：纯标点样本（n-gram 也会命中，但要确认标点判据自己命中）
        for tag, txt in {
            "纯标点单段": TITLE + "。，！？；：、……——" * 200,
            "纯标点8段": TITLE + "\n\n".join(["。，！？；：、……——" * 20] * 8),
        }.items():
            problems, metrics = check_degenerate(txt)
            check(f"C1 {tag} 标点判据独立命中",
                  any("标点灌水" in x for x in problems),
                  f"→ content_ratio={metrics.get('content_ratio')} {problems}")

        # C2 阈值边界：元话语 25% 上下
        # ⚠️ 样本必须撑过 MIN_RATIO_BODY_CHARS 门槛，否则比率判据被整体跳过，
        #    会得到 meta_ratio=None 的假失败（开发时踩过）。
        filler_line = "他推开门，风从走廊尽头卷过来，带着旧纸和雨水的气味。"
        for ratio, should_hit in ((0.15, False), (0.35, True)):
            n_total = 40
            n_meta = int(n_total * ratio)
            metas = [
                "作为一个AI助手，我无法完成这个章节。",
                "很抱歉，我不能提供这样的内容。",
                "如果您需要，我可以提供大纲。",
                "以下是您需要内容的说明。",
                "作为语言模型，我需要提醒您自行撰写。",
            ]
            body = "\n\n".join(
                [metas[i % len(metas)] for i in range(n_meta)]
                + [filler_line] * (n_total - n_meta)
            )
            problems, metrics = check_degenerate(TITLE + body)
            got = any("元话语" in x for x in problems)
            check(f"C2 元话语 {ratio:.0%} → {'命中' if should_hit else '不命中'}",
                  got == should_hit,
                  f"→ meta_ratio={metrics.get('meta_ratio')} "
                  f"body_chars={metrics.get('body_chars')} problems={problems}")

        # C3 短章不判比率型判据（< MIN_RATIO_BODY_CHARS）
        short = TITLE + "作为一个AI助手，我无法完成这个章节。\n\n" * 3
        problems, metrics = check_degenerate(short)
        check("C3 短正文跳过比率型判据",
              metrics.get("ratio_skipped") is True
              and not any("元话语" in x for x in problems),
              f"→ body_chars={metrics.get('body_chars')} {problems}")
        check("C3b 短正文仍跑段落结构判据",
              any("段落过少" in x for x in problems), f"→ {problems}")

        # ============ D. 边界与鲁棒性 ============
        print("\n=== D. 边界与鲁棒性 ===")
        for tag, txt in {
            "空文本": "",
            "仅标题": TITLE,
            "纯空行": "\n\n\n\n",
            "仅空白符": "   \n\t\n  ",
        }.items():
            problems, metrics = check_degenerate(txt)
            check(f"D1 {tag} 判为空正文",
                  len(problems) >= 1 and "正文为空" in problems[0],
                  f"→ {problems}")

        # 正常短正文（对话密集风格）不应被误判
        dialog = TITLE + "\n\n".join([
            '"坐。"', "暮雨选了靠门的位子。", '"叙旧。"白莲说。', "暮雨没碰茶。",
            '"风灵姐姐让我来的。"', '"我知道。"白莲给自己倒了杯。',
            "暮雨看了茶壶三秒。", '"最近忙什么？"白莲问。', '"沙都。第七区。"',
            '"那边不太平。"', '"嗯。"', '"去了多久？"', "第三杯茶。暮雨说了一个数字。",
            "白莲没接话。", "暮雨吃了。", '"你最近睡得好吗？"白莲问。',
            '"不睡觉。"', "白莲没说人偶不需要睡眠。", '"那就多喝点。"',
        ])
        problems, metrics = check_degenerate(dialog)
        check("D2 对话密集短句成段不误判",
              not any("平均段长" in x or "离散度" in x for x in problems),
              f"→ {problems}")

        # 标题行 / 质量注释 / 分隔线不得计入段落
        problems, metrics = check_degenerate(
            "## 第9章 X\n\n<!-- quality: 8/10 -->\n\n---\n\n***\n\n" + "\n\n".join(_POOL))
        check("D3 标题/注释/分隔线不计入正文段落",
              metrics.get("paragraphs") == len(_POOL),
              f"→ paragraphs={metrics.get('paragraphs')} 期望={len(_POOL)}")

        # ============ E. is_chapter_complete 集成 ============
        print("\n=== E. is_chapter_complete 集成（断点续跑防线） ===")
        for name in DEGEN_SAMPLES:
            p = _make_sample(name, tmpdir)
            # 用宽松字数区间排除字数干扰，只测退化这一项
            check(f"E1 {name} 被判为未完成",
                  is_chapter_complete(p, 1000, 4000) is False,
                  f"→ 字数={ChapterCheck(str(p)).word_count}")
        # 真实正样本仍判完成
        real_good = [f for f in _real_chapters() if f.name == "01.md" and "checked" in str(f)]
        if real_good:
            check("E2 真实正样本仍判为完成",
                  is_chapter_complete(real_good[0], 1000, 4000) is True,
                  f"→ {real_good[0]}")

        # ============ F. 反向验证：删判据 → 必须变绿 ============
        print("\n=== F. 反向验证（删掉判据 → 对应样本必须不再命中） ===")
        import importlib
        orig = {
            "ngram": vc.MAX_NGRAM_REPEAT_RATIO,
            "single": vc.MAX_SINGLE_PARA_RATIO,
            "paras": vc.MIN_PARAGRAPHS,
            "ratio_gate": vc.MIN_RATIO_BODY_CHARS,
        }
        REVERSE = [
            # (判据名, 猴子补丁, 用哪个样本, 样本里哪个关键词必须消失)
            ("复读 n-gram", {"MAX_NGRAM_REPEAT_RATIO": 1.0}, "复读填充", "复读"),
            ("段落数下限", {"MIN_PARAGRAPHS": 0}, "无段落换行", "段落过少"),
            ("单段占比上限", {"MAX_SINGLE_PARA_RATIO": 1.0}, "无段落换行", "单一超长段落"),
            ("元话语阈值", None, "元话语拒答", "元话语"),
            ("思考残片阈值", None, "思考残片", "思考残片"),
            ("骨架阈值", None, "模板骨架", "模板骨架"),
            ("标点阈值", None, "标点灌水", "标点灌水"),
        ]
        for judge, patch, sname, keyword in REVERSE:
            backup = {k: getattr(vc, k) for k in patch} if patch else None
            try:
                if patch:
                    for k, v in patch.items():
                        setattr(vc, k, v)
                else:
                    # 模式类判据：清空该模式表即为「删掉该判据」
                    attr = {"元话语阈值": "META_PATTERNS",
                            "思考残片阈值": "REASONING_PATTERNS",
                            "骨架阈值": "SKELETON_PATTERNS",
                            "标点阈值": None}[judge]
                    if attr:
                        backup = {attr: getattr(vc, attr)}
                        setattr(vc, attr, [])
                    else:
                        backup = {"MIN_RATIO_BODY_CHARS": vc.MIN_RATIO_BODY_CHARS}
                        vc.MIN_RATIO_BODY_CHARS = 10 ** 9  # 让标点判据被门槛挡死

                p = Path(tmpdir) / f"{sname}.md"
                problems, _ = vc.check_degenerate(p.read_text(encoding="utf-8"))
                still = any(keyword in x for x in problems)
                check(f"F1 删「{judge}」→ {sname} 的「{keyword}」消失",
                      not still, f"→ 仍命中: {problems}")
            finally:
                if backup:
                    for k, v in backup.items():
                        setattr(vc, k, v)

        # 恢复后必须重新命中（证明是判据而非环境在起作用）
        importlib.reload(vc)
        for sname, keyword in (("复读填充", "复读"), ("元话语拒答", "元话语"),
                               ("模板骨架", "模板骨架"), ("标点灌水", "标点灌水")):
            p = Path(tmpdir) / f"{sname}.md"
            problems, _ = vc.check_degenerate(p.read_text(encoding="utf-8"))
            check(f"F2 恢复后 {sname} 重新命中「{keyword}」",
                  any(keyword in x for x in problems), f"→ {problems}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}"
          + (f"，失败 {len(FAIL)}" if FAIL else ""))
    if FAIL:
        for f in FAIL:
            print(f"  FAIL: {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
