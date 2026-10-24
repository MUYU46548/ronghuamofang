# -*- coding: utf-8 -*-
"""样例书「大纲 ↔ 素材」自洽性回归锁（2026-10-18）。

## 这条锁拦的是什么

`examples/sample-book/` 是随包出厂的固定测试示例书：书名《雾港核心》，
`project.yaml` 的 `user_outline` 讲**沈砚 / 程砧 / 机关术 / 雾港核心封印**。

2026-10-03 提交 `91b6c78`「发布树虚构化」把 19 张素材卡整套换成了**另一个宇宙**
（潮神 / 鲸民 / 月宫 / 守灯会），`user_outline` 未同步 —— 于是这本出厂书自相矛盾：

  大纲要写沈砚去雾港遗址 → 设定集里却是潮神怎么当月之神

实测用户工作区跑完 stage1 的 `setting.json`：16 个角色只有 2 个属于大纲，
18 个地点 **0 个**属于大纲。模型拿着别人的设定写这本书，成品质量崩坏，
而 `nfctl check` / 全流程门禁**全程绿灯** —— 因为没有任何判据检查「大纲与素材配不配套」。

## 判据

1. 样例卡仍是 19 张（README 与 factory 域的文档字符串都写死了这个数）
2. `user_outline` 里的专有名词，**每一个**都能在某张素材卡里找到落点
3. 旧宇宙的词一个都不许残留（防止将来再做虚构化替换时只换一半）
4. 每张卡都带「非真实作品」脱敏声明（虚构化纪律）
5. 每张卡首行是合法标题（`# 类型：名字` 或 `## 标题`）
6. 卡内无本机绝对路径（虚构化纪律）

用法：python tests/unit/test_sample_book_coherence.py
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(ROOT)
SAMPLE = os.path.join(ROOT, "examples", "sample-book")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:180]) if detail else ""))


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def main():
    print("=" * 62)
    print("  样例书自洽性：大纲 ↔ 素材卡")
    print("=" * 62)

    py = os.path.join(SAMPLE, "project.yaml")
    check("样例 project.yaml 存在", os.path.isfile(py), py)
    if not os.path.isfile(py):
        return 1

    # 用宽松解析：样例配置里有中文块标量，严格 loader 也应能读，但这里只需 user_outline
    try:
        import yaml
        proj = yaml.safe_load(_read(py)) or {}
    except Exception as e:                                    # noqa: BLE001
        check("样例 project.yaml 可解析", False, "%s: %s" % (type(e).__name__, e))
        return 1
    check("样例 project.yaml 可解析", True)

    outline = ((proj.get("book") or {}).get("user_outline") or "").strip()
    check("user_outline 非空", bool(outline))
    print("\n  user_outline: %s" % outline[:120])

    # 素材卡住在 `materials/raw/` —— 与 stage1 的 `scan_materials` 消费路径**同一层**。
    # 曾经卡直接躺 `materials/` 根，于是「拿样例书直接试跑」stage1 扫到 0 张卡、
    # 归并出空设定集，而样例自洽性判据全绿（它自己读 materials/*.md，与消费路径不是同一条）。
    mat = os.path.join(SAMPLE, "materials")
    raw = os.path.join(mat, "raw")
    check("样例 materials/raw/ 存在（stage1 只扫这一层）", os.path.isdir(raw), raw)
    files = sorted(f for f in os.listdir(raw) if f.endswith(".md")) if os.path.isdir(raw) else []
    check("样例卡仍是 19 张", len(files) == 19, len(files))

    # 判据 1b：卡不许落在 materials/ 直下（放错层级 = 静默扫零，比缺失更难查）
    stray = sorted(f for f in os.listdir(mat) if f.endswith(".md")) if os.path.isdir(mat) else []
    check("卡不在 materials/ 直下（否则 stage1 一张都扫不到）", not stray, stray)

    corpus = "".join(_read(os.path.join(raw, f)) for f in files)

    # ---- 判据 2：大纲实体必须都有素材落点 ----
    required = ["沈砚", "程砧", "祝鹤川", "雾港核心", "雾港遗址", "机关术",
                "七锁回环", "封印石", "玄翎卫", "边城守卫队", "机关黑市",
                "灵力读数图", "核心舱", "机关兽", "边城废屋"]
    missing = [k for k in required if k not in corpus]
    check("大纲实体全部有素材落点", not missing, missing)
    for k in required:
        print("      %-12s %s" % (k, "OK" if k in corpus else "缺失"))

    # ---- 判据 3：旧宇宙的词不许残留 ----
    stale = ["潮神", "鲸民", "守灯会", "白蚀", "休止锚", "月宫", "澄源司",
             "断潮部队", "织术者", "塞尔", "镜铃", "回声族", "潮崩", "穹顶"]
    leaked = [w for w in stale if w in corpus]
    check("旧宇宙词已清空（虚构化不得只换一半）", not leaked, leaked)

    # ---- 判据 4：脱敏声明 ----
    nohdr = [f for f in files if "非真实作品" not in _read(os.path.join(raw, f))]
    check("每张卡都带「非真实作品」脱敏声明", not nohdr, nohdr)

    # ---- 判据 5：首行合法标题 ----
    bad = []
    for f in files:
        first = _read(os.path.join(raw, f)).split("\n", 1)[0].strip()
        if not (first.startswith("# ") or first.startswith("## ")):
            bad.append((f, first[:40]))
    check("每张卡首行是合法标题", not bad, bad)

    # ---- 判据 6：无本机绝对路径 ----
    badpath = [f for f in files
               if re.search(r"[A-Za-z]:\\", _read(os.path.join(raw, f)))
               or "/home/" in _read(os.path.join(raw, f))
               or "/Users/" in _read(os.path.join(raw, f))]
    check("卡内无本机绝对路径", not badpath, badpath)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
