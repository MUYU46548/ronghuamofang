# -*- coding: utf-8 -*-
"""旧段落池判据（2026-11-14，P1=A）。

守五件事：
  1. **只收废稿**：读归档书档，每章只取一层（refined > checked > raw），不出三份重复；
  2. **太短的不进**：30 汉字以下的过场句不进池（否则检索结果全被淹）；
  3. **幂等**：同样的盘上内容重建两次，id 集合一致（外部引用的 id 不会失效）；
  4. **不给全量**：没有关键词/人物/地名时返回空 + 提示，不一次性吐一千段；
  5. **不自动进写作**：提示词模板里不出现段落池 —— 这是“只在我要用时才召回”的硬落点。

用法：python tests/unit/test_paragraph_pool.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import paragraph_pool as pp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if extra else ""))


LONG = ("风从三道口子灌进来，带着卤水沤过的腥气，贴在脸上像一层薄壳。岑砚站在渡口的石阶上，"
        "把箱子抱在怀里先掂了掂，再贴到耳边听，里面没有声音。他转身往盐埕走去。")
SHORT = "他走了。"


def book(root: Path, name: str, chapters: dict, people=("岑砚",)) -> Path:
    """造一本书档：chapters = {章号: {层: 正文}}。"""
    b = root / "data" / "books" / "_trash" / name
    (b / "setting").mkdir(parents=True, exist_ok=True)
    (b / "_meta.json").write_text('{"title": "%s"}' % name, encoding="utf-8")
    (b / "setting" / "setting.json").write_text(
        '{"characters": [{"name": "%s"}], "world": {"locations": [{"name": "盐埕"}]}}'
        % people[0], encoding="utf-8")
    for n, layers in chapters.items():
        for layer, text in layers.items():
            d = b / "chapters" / layer
            d.mkdir(parents=True, exist_ok=True)
            (d / ("%02d.md" % n)).write_text("## 第%d章\n\n%s\n\n%s\n" % (n, LONG, SHORT),
                                             encoding="utf-8")
    return b


def case_build_and_stats():
    print("\n[A] 建索引：只收废稿、每章一层、短段不进")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        book(root, "甲书__20260101_000000", {1: {"raw": "x", "checked": "x", "refined": "x"},
                                             2: {"raw": "x"}})
        book(root, "乙书__20260101_000000", {1: {"checked": "x"}})
        st = pp.build(root)
        check("A1 两本书都扫到（_trash 嵌套也算）", st["books"] == 2, st)
        check("A2 章数 3", st["chapters"] == 3, st)
        check("A3 只有长段入选（每章 1 段）", st["paragraphs"] == 3, st)
        check("A4 短段被记进 skipped_short（含章节标题行）", st["skipped_short"] >= 3, st)

        items = pp.load(root)["items"]
        layers = {(it["book"], it["chapter"]): it["layer"] for it in items}
        check("A5 三层都有时取 refined",
              layers[("甲书__20260101_000000", 1)] == "refined", layers)
        check("A6 只有 raw 的章取 raw",
              layers[("甲书__20260101_000000", 2)] == "raw", layers)
        check("A7 只有 checked 的章取 checked",
              layers[("乙书__20260101_000000", 1)] == "checked", layers)
        check("A8 id 形如 书名#章号#段号",
              all(it["id"].count("#") == 2 for it in items), items[0]["id"])
        check("A9 人物/地名标签打上了",
              "岑砚" in items[0]["tags"]["people"] and "盐埕" in items[0]["tags"]["places"],
              items[0]["tags"])

        ids1 = sorted(it["id"] for it in items)
        pp.build(root)
        ids2 = sorted(it["id"] for it in pp.load(root)["items"])
        check("A10 幂等：重建两次 id 集合一致", ids1 == ids2, (len(ids1), len(ids2)))
        check("A11 只写自己的索引（没在书档里拉屎）",
              not list((root / "data" / "books").rglob("index.json")))


def case_search():
    print("\n[B] 检索：条件齐才查、去重、按人物过滤")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        book(root, "甲书", {1: {"refined": "x"}})
        book(root, "乙书", {1: {"refined": "x"}})   # 正文完全相同 → 去重应只留一本
        pp.build(root)
        r = pp.search(root)
        check("B1 空查询不给全量，并说清为什么",
              r["total"] == 0 and bool(r.get("hint")), r.get("hint"))
        r = pp.search(root, q="卤水")
        check("B2 关键词命中", r["total"] >= 1, r["total"])
        check("B3 同一段文本跨书只留一条（去重）", r["total"] == 1, r["total"])
        r2 = pp.search(root, q="卤水", dedupe=False)
        check("B4 关掉去重能看到两条（证明去重真的在起作用）", r2["total"] == 2, r2["total"])
        r = pp.search(root, person="岑砚")
        check("B5 按人物过滤命中", r["total"] >= 1, r["total"])
        r = pp.search(root, person="不存在的人")
        check("B6 查不存在的人给 0", r["total"] == 0, r["total"])
        r = pp.search(root, q="这句话根本不存在")
        check("B7 查不存在的词给 0", r["total"] == 0, r["total"])
        fac = pp.facets(root)
        check("B8 facets 只列索引里真出现过的人物",
              [x[0] for x in fac["people"]] == ["岑砚"], fac["people"])
        r = pp.search(root, q="卤水", limit=1)
        check("B9 limit 生效", len(r["items"]) == 1, len(r["items"]))


def case_not_in_prompts():
    print("\n[C] 不自动进写作：提示词模板里不许出现段落池")
    prompts = REPO / "prompts"
    hits = []
    for p in sorted(prompts.glob("*.md")):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:                                      # noqa: BLE001
            continue
        if "paragraph_pool" in t or "旧段落池" in t:
            hits.append(p.name)
    check("C1 提示词模板零引用（P1=A：只在作者要用时才召回）", not hits, hits)


for fn in (case_build_and_stats, case_search, case_not_in_prompts):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
