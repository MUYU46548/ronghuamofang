# -*- coding: utf-8 -*-
"""二号测试用例（《炭城纪》）的自洽性回归锁（2026-10-19）。

## 它和 `test_sample_book_coherence.py` 的分工

那一条锁的是**出厂样例**（《雾港核心》↔ 沈砚/机关术）——它证明"第一套素材配套"。
这一条锁的是 `tests/fixtures/sample-book-2/`：一个**第三个虚构宇宙**的完整素材集，
用来证明判据不依赖具体专名（换一整套名字照样成立），同时防止将来做虚构化替换时
只换一半 —— 那样第二套夹具会悄悄变回《雾港纪年》的皮。

## 判据

1. `project.yaml` 可解析，`user_outline` 非空
2. 素材卡仍是 **19 张**（与源素材一致，少一张说明改名/移动掉了文件）
3. `user_outline` 的每个专名都能在某张卡里找到落点
4. 前面两个宇宙（《雾港纪年》《雾港核心》）的词**一个都不许残留**
5. 每张卡都带「非真实作品」脱敏声明
6. 每张卡首行是合法标题
7. 卡内无本机绝对路径
8. **文件名与卡内专名一致**（改名不得只改一半）

用法：python tests/unit/test_sample_book_2_coherence.py（零 LLM、零网络）
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(ROOT)
SAMPLE = os.path.join(ROOT, "tests", "fixtures", "sample-book-2")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def main():
    print("=" * 62)
    print("  二号用例自洽性：大纲 ↔ 素材卡（《炭城纪》）")
    print("=" * 62)

    py = os.path.join(SAMPLE, "project.yaml")
    check("二号用例 project.yaml 存在", os.path.isfile(py), py)
    if not os.path.isfile(py):
        return 1

    try:
        import yaml
        proj = yaml.safe_load(_read(py)) or {}
    except Exception as e:                                    # noqa: BLE001
        check("project.yaml 可解析", False, "%s: %s" % (type(e).__name__, e))
        return 1
    check("project.yaml 可解析", True)

    outline = ((proj.get("book") or {}).get("user_outline") or "").strip()
    check("user_outline 非空", bool(outline))
    if not outline:
        return 1
    print("\n  user_outline: %s" % outline[:140])

    mat = os.path.join(SAMPLE, "materials", "raw")
    check("materials/raw/ 存在", os.path.isdir(mat), mat)
    files = sorted(f for f in os.listdir(mat) if f.endswith(".md")) if os.path.isdir(mat) else []
    check("素材卡仍是 19 张", len(files) == 19, len(files))
    if not files:
        return 1

    corpus = "".join(_read(os.path.join(mat, f)) for f in files)

    # ---- 判据 3：大纲实体必须都有素材落点 ----
    required = ["裴砚", "禾芷", "炭城", "窑主", "陶民", "添薪会", "截火部队",
                "陶律者公约会", "静窑院", "陶民密室", "窑心", "盐蚀", "窑崩",
                "判律", "熄炉锚", "匀火司", "陶简"]
    missing = [k for k in required if k not in corpus]
    check("大纲实体全部有素材落点", not missing, missing)
    for k in required:
        print("      %-14s %s" % (k, "OK" if k in corpus else "缺失"))

    # ---- 判据 4：前两个宇宙的词一个都不许残留 ----
    stale = ["雾港纪年", "雾港核心", "雾港", "潮神", "潮崩", "潮简", "潮枢府",
             "潮汐", "鲸民", "守灯会", "白蚀", "休止锚", "穹顶", "澄源司",
             "断潮部队", "织术", "灰羽帝国", "折镜", "幽堤", "青崖", "夏绾",
             "塔洛斯", "驭风者", "塞尔", "镜铃", "回声", "祁砚", "苏芷",
             "云芥", "商羽", "观澜", "苔薇", "廪务部", "疗心院", "阿苔",
             "老赵", "空舰", "无人船", "裁定", "沈砚", "程砧", "机关术",
             "玄翎卫", "祝鹤川", "七锁回环", "封印石"]
    leaked = [w for w in stale if w in corpus]
    check("两个旧宇宙的词已清空（改名不得只改一半）", not leaked, leaked)

    # ---- 判据 5：脱敏声明 ----
    nohdr = [f for f in files if "非真实作品" not in _read(os.path.join(mat, f))]
    check("每张卡都带「非真实作品」脱敏声明", not nohdr, nohdr)

    # ---- 判据 6：首行合法标题 ----
    bad = []
    for f in files:
        first = _read(os.path.join(mat, f)).split("\n", 1)[0].strip()
        if not (first.startswith("# ") or first.startswith("## ")):
            bad.append((f, first[:40]))
    check("每张卡首行是合法标题", not bad, bad)

    # ---- 判据 7：无本机绝对路径 ----
    badpath = []
    for f in files:
        t = _read(os.path.join(mat, f))
        if re.search(r"[A-Za-z]:\\", t) or "/home/" in t or "/Users/" in t:
            badpath.append(f)
    check("卡内无本机绝对路径", not badpath, badpath)

    # ---- 判据 8：文件名与卡内专名一致 ----
    drift = []
    for f in files:
        stem = os.path.splitext(f)[0].split("_")[0]
        if stem and stem not in _read(os.path.join(mat, f)):
            drift.append((f, stem))
    check("文件名与卡内专名一致（改名不得只改一半）", not drift, drift)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
