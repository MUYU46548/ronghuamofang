# -*- coding: utf-8 -*-
"""出厂预设用例的自洽性锁（2026-10-19）。

## 这条锁拦的是什么

`examples/presets/` 是随包出厂的能力验证用例：每套一份 `preset.yaml` +
`materials/raw/*.md` + `README.md`。它们不是文档，是**可跑的验收起点**。

历史上同类东西出过事：2026-10-03「发布树虚构化」把 19 张素材卡整套换成另一个宇宙，
`user_outline` 没跟着换，写沈砚的章喂进去的是潮神设定，而全流程门禁**全程绿灯**。
所以本用例只认一条硬判据：

  **preset.yaml 里点名要出现的专名，必须在自己的素材卡里真的出现。**

反向判据（会失败的反证）：
- 把某张素材删掉 → `min_cards` 立刻红
- 把 must_mention 里加一个素材中不存在的词 → 立刻红
- 把别套的专名塞进这套素材 → 跨宇宙隔离立刻红

用法：python tests/unit/test_presets.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRESETS = os.path.join(ROOT, "examples", "presets")
CARD_SUFFIXES = ("角色卡", "场景卡", "物品卡", "概念卡", "组织卡")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  <- " + detail) if (detail and not cond) else ""))


def load_yaml(path):
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read(p):
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def card_files(raw_dir):
    if not os.path.isdir(raw_dir):
        return []
    return sorted(
        os.path.join(raw_dir, n) for n in os.listdir(raw_dir)
        if n.lower().endswith(".md") and os.path.isfile(os.path.join(raw_dir, n))
    )


def main():
    print("== 出厂预设自洽性 ==")
    if not os.path.isdir(PRESETS):
        check("presets 目录存在", False, PRESETS)
        return 1

    dirs = sorted(d for d in os.listdir(PRESETS) if os.path.isdir(os.path.join(PRESETS, d)))
    check("至少 4 套预设", len(dirs) >= 4, "实际 %d 套" % len(dirs))

    metas = {}
    for d in dirs:
        p = os.path.join(PRESETS, d, "preset.yaml")
        if not os.path.isfile(p):
            check("%s 有 preset.yaml" % d, False)
            continue
        try:
            meta = load_yaml(p)
        except Exception as e:
            check("%s preset.yaml 可解析" % d, False, str(e)[:60])
            continue
        metas[d] = meta

    check("至少 4 套可解析的预设", len(metas) >= 4, "实际 %d" % len(metas))

    hidden = [d for d, m in metas.items() if m.get("hidden")]
    visible = [d for d, m in metas.items() if not m.get("hidden")]
    check("非隐藏预设 >= 4 套", len(visible) >= 4, "实际 %d" % len(visible))
    check("隐藏用例 >= 1 套", len(hidden) >= 1, "实际 %d" % len(hidden))

    all_forbidden = {}
    for d, m in metas.items():
        all_forbidden[d] = [w for w in (m.get("verify") or {}).get("forbidden") or [] if w]

    for d in sorted(metas):
        m = metas[d]
        v = m.get("verify") or {}
        print("")
        print("-- %s（%s）" % (d, m.get("name")))

        check("%s id 与目录名一致" % d, (m.get("id") or d) == d, "id=%s" % m.get("id"))
        for key in ("name", "genre", "chapters", "user_outline", "verify"):
            check("%s 有 %s" % (d, key), bool(m.get(key)), "缺字段")

        ch = m.get("chapters")
        check("%s chapters 是 1-999 的整数" % d, isinstance(ch, int) and 1 <= ch <= 999, "值=%r" % (ch,))

        min_w = v.get("min_chapter_words")
        check("%s min_chapter_words 在 500-20000" % d,
              isinstance(min_w, int) and 500 <= min_w <= 20000, "值=%r" % (min_w,))

        outline = (m.get("user_outline") or "").strip()
        check("%s user_outline 长度 >= 80" % d, len(outline) >= 80, "%d 字" % len(outline))

        raw = os.path.join(PRESETS, d, "materials", "raw")
        cards = card_files(raw)
        need = int(v.get("min_cards") or 1)
        check("%s 素材卡数 >= %d" % (d, need), len(cards) >= need, "实际 %d" % len(cards))

        texts = {}
        for c in cards:
            t = read(c)
            texts[os.path.basename(c)] = t
            check("%s/%s 正文 >= 100 字" % (d, os.path.basename(c)), len(t.strip()) >= 100, "%d 字" % len(t.strip()))

        blob = "\n".join(texts.values())

        # 核心判据：大纲点名的专名必须在素材里有落点
        own_must = set(v.get("must_mention") or [])
        for word in own_must:
            check("%s 素材含「%s」" % (d, word), word in blob)

        # 本套禁词不得出现（如"不得注入奇幻套话"）
        for w in v.get("forbidden") or []:
            check("%s 素材不含禁词「%s」" % (d, w), w not in blob)

        # 跨宇宙隔离：**别套点名的专名**不许出现在本套里。
        # 注意这里必须用别套的 must_mention，不能用别套的 forbidden ——
        # 别套的 forbidden 里往往含**本套自己的名字**（"我不许出现你的词"），
        # 拿它来判本套会 100% 假红（首版就是这么写错的，12 条假红）。
        for other, m2 in metas.items():
            if other == d:
                continue
            other_must = (m2.get("verify") or {}).get("must_mention") or []
            for w in other_must:
                if w in own_must:
                    continue
                check("%s 不残留 %s 的专名「%s」" % (d, other, w), w not in blob)

        # 非隐藏套必须格式规整（要能给用户当演示）；隐藏套必须真的脏
        no_suffix = [n for n in texts if not any(s in n for s in CARD_SUFFIXES)]
        if m.get("hidden"):
            need_dirty = int(v.get("dirty_files") or 1)
            check("%s 脏文件数 >= %d（隐藏用例的测试价值）" % (d, need_dirty),
                  len(no_suffix) >= need_dirty, "实际 %d: %s" % (len(no_suffix), no_suffix[:3]))
        else:
            check("%s 每张卡都有类型后缀" % d, not no_suffix, "缺后缀: %s" % no_suffix[:3])

        check("%s 有 README.md" % d, os.path.isfile(os.path.join(PRESETS, d, "README.md")))

    print("")
    print("== 结果: %d PASS / %d FAIL ==" % (len(PASS), len(FAIL)))
    if FAIL:
        for n in FAIL:
            print("  FAIL: %s" % n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
