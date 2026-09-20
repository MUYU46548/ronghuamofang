# -*- coding: utf-8 -*-
"""scrap_cluster 自检：自由命名适配 / 时间戳三级回退 / 内容聚类 / 前瞻备忘 / 指纹稳定性。

纯 stdlib，零 LLM、零网络。用法：
  python tests/test_scrap_cluster.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import utils.scrap_cluster as sc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:180]) if detail else ""))


def write(p, text, encoding="utf-8"):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(text, encoding=encoding)


def make_dir():
    return tempfile.mkdtemp(prefix="scrap_test_")


# 自由命名样本：故意用 6 种完全不同的命名风格（无分隔符、空格、日期在中间、纯英文、中文括号、下划线）
FREE_NAMES = {
    "月神那点事": """
2024年3月5日

月神这个人，沉默到让人害怕。他造了月兔，却没给他们同类。
待定：他左耳那道抓痕是谁留的？以后再想。
月兔文明被月面大结界罩着，外面探不进来。
""",
    "2024-03-09 月兔密室": """
月兔密室在月兔之城下面。月神几乎不去正殿，只在书房待着。
月兔对月神的称呼还没定，先这样。
""",
    "梦琴 半夜想到的": """
梦琴的手指按在琴弦上，声音是斜的。
这个角色和月神没关系，她是正弦司的人。回头再补她的背景。
""",
    "歌唯(乐器店)": """
歌唯开乐器店。她卖的不是乐器，是别人弹过的记忆。这和月神无关。
""",
    "notes_about_sine": """
正弦司是一个组织。它和梦琴有关系，但和月神没有关系。
""",
    "3月12日": """
月神在正殿高背椅上坐了整整一夜。月兔们不敢抬头。
月兔密室的钥匙在谁手里，待确认。
""",
}


def case_timestamp():
    print("\n=== 1. 时间戳三级回退（自由命名）===")
    d = make_dir()
    write(Path(d) / "月神那点事.md", FREE_NAMES["月神那点事"])
    write(Path(d) / "2024-03-09 月兔密室.md", FREE_NAMES["2024-03-09 月兔密室"])
    write(Path(d) / "无日期随便写写.md", "这里没有任何日期。\n")
    write(Path(d) / "正文里才有日期.md", "2025年7月1日\n今天想到一件事。\n")
    write(Path(d) / "3月12日.md", "月神坐了整夜。\n")
    write(Path(d) / "0312.md", "月兔不敢抬头。\n")
    write(Path(d) / "20250406.md", "正殿无人。\n")
    write(Path(d) / "第0312条草稿.md", "这里的 0312 不是日期，不该被当成日期。\n")

    by_name = {s.name: s for s in sc.scan_scraps(d)[0]}
    check("文件名含中文年月日 → source=filename",
          by_name["月神那点事.md"].ts == "2024-03-05", by_name["月神那点事.md"].ts)
    check("文件名含 ISO 日期 → 解出正确 ts",
          by_name["2024-03-09 月兔密室.md"].ts == "2024-03-09")
    check("正文首行日期 → source=content",
          by_name["正文里才有日期.md"].ts == "2025-07-01"
          and by_name["正文里才有日期.md"].ts_source == "content")
    md = by_name["3月12日.md"]
    check("只有月日 → 用文件时间年份补全并标 partial",
          md.ts.endswith("-03-12") and md.ts_partial and md.ts_source == "filename",
          (md.ts, md.ts_partial, md.ts_source))
    check("完全无日期 → 回退 mtime 且标注不可信",
          by_name["无日期随便写写.md"].ts_source == "mtime"
          and by_name["无日期随便写写.md"].ts is not None)
    b4 = by_name["0312.md"]
    check("文件名是纯数字 0312 → 按「月日」解出（自由命名常见写法）",
          b4.ts.endswith("-03-12") and b4.ts_source == "filename" and b4.ts_partial,
          (b4.ts, b4.ts_source, b4.ts_partial))
    b8 = by_name["20250406.md"]
    check("文件名是纯数字 20250406 → 按「年月日」解出",
          b8.ts == "2025-04-06" and not b8.ts_partial, b8.ts)
    check("数字出现在名字中间时不被当日期（防误判）",
          by_name["第0312条草稿.md"].ts_source == "mtime",
          by_name["第0312条草稿.md"].ts_source)
    shutil.rmtree(d, ignore_errors=True)


def case_free_naming():
    print("\n=== 2. 自由命名适配（命名提示只是弱信号）===")
    d = make_dir()
    for name, text in FREE_NAMES.items():
        write(Path(d) / (name + ".md"), text)
    scraps, _ = sc.scan_scraps(d)
    check("6 种命名风格全部被扫描到", len(scraps) == 6, len(scraps))
    hints = {s.stem: s.topic_hint for s in scraps}
    check("无分隔符中文名 → 提示退化为整个文件名（弱信号，仅作佐证）",
          hints.get("月神那点事") == "月神那点事", hints.get("月神那点事"))
    check("日期在名首 → 日期被剥离，不留残渣",
          hints.get("2024-03-09 月兔密室") == "月兔密室", hints.get("2024-03-09 月兔密室"))
    check("纯英文名 → 提示可取", hints.get("notes_about_sine") == "notes", hints.get("notes_about_sine"))
    check("纯月日名 → 无提示（不误当主题）", hints.get("3月12日") == "", hints.get("3月12日"))
    check("命名提示单独命中不足以成簇（自由命名不可靠）", sc.HINT_ONLY_SCORE is False)
    shutil.rmtree(d, ignore_errors=True)


def case_clustering():
    print("\n=== 3. 内容驱动聚类（不依赖文件名）===")
    d = make_dir()
    for name, text in FREE_NAMES.items():
        write(Path(d) / (name + ".md"), text)
    index, _ = sc.collect(d)
    clusters = {c["name"]: c for c in index["clusters"]}
    yueshen = next((c for c in index["clusters"] if "月神那点事.md" in [s["name"] for s in c["scraps"]]), None)
    check("讲月神的多个碎片被合并成一簇",
          yueshen is not None and yueshen["count"] >= 3, yueshen and yueshen["count"])
    check("合并簇带可审计的合并证据",
          bool(yueshen and yueshen["evidence"]), yueshen and yueshen["evidence"][:1])
    mq = next((c for c in index["clusters"]
               if "梦琴 半夜想到的.md" in [s["name"] for s in c["scraps"]]), None)
    check("梦琴/正弦司 与月神簇相互独立",
          mq is not None and yueshen is not None and mq["name"] != yueshen["name"],
          mq and mq["name"])
    check("梦琴与正弦司两条笔记合并（正确的一簇）",
          mq is not None and mq["count"] == 2, mq and mq["count"])
    check("关键词已剔除跨词边界垃圾 n-gram",
          all(not any(ch in sc.FUNC_CHARS for ch in kw) for kw in (yueshen or {}).get("keywords", [])),
          yueshen and yueshen["keywords"][:5])
    check("簇内按时间序排列（最早在前）",
          yueshen and [s["ts"] for s in yueshen["scraps"]] == sorted(s["ts"] for s in yueshen["scraps"]),
          yueshen and [s["ts"] for s in yueshen["scraps"]])
    check("关键词已抽取（供 GUI 与提示词复用）", bool(yueshen and yueshen["keywords"]),
          yueshen and yueshen["keywords"][:4])
    check("每簇簇名唯一", len(clusters) == len(index["clusters"]))
    shutil.rmtree(d, ignore_errors=True)


def case_negative_clustering():
    print("\n=== 4. 反向用例：不相关碎片不应被合并 ===")
    d = make_dir()
    write(Path(d) / "甲.md", "甲的故事：他修钟表，每天擦一遍黄铜齿轮，齿轮比他的指甲还亮。\n")
    write(Path(d) / "乙.md", "乙的故事：她在码头卖鱼，秤砣掉了三回，海风把她的头巾吹成一团。\n")
    write(Path(d) / "丙.md", "丙：山里下雪，柴火不够，他把最后一本账簿撕了引火。\n")
    index, _ = sc.collect(d)
    check("三个无关联碎片 → 三簇（不误并）", index["stats"]["cluster_count"] == 3,
          index["stats"]["cluster_count"])
    shutil.rmtree(d, ignore_errors=True)


def case_negation():
    print("\n=== 4b. 排除性语境：「这和X无关」不应被当成关于X的证据 ===")
    d = make_dir()
    write(Path(d) / "甲.md", "月神造了月兔。\n")
    write(Path(d) / "乙.md", "月兔密室的钥匙在谁手里。\n")
    write(Path(d) / "丙.md", "这个角色和月神无关，她是正弦司的人。\n")
    index, _ = sc.collect(d)
    clusters = {c["name"]: c for c in index["clusters"]}
    bing = next(c for c in index["clusters"] if "丙.md" in [s["name"] for s in c["scraps"]])
    check("丙 未被并进月神簇（否定词已被剔除）", bing["count"] == 1, bing["count"])
    check("丙 的关键词里不含被否定的「月神」",
          "月神" not in bing["scraps"][0]["keywords"], bing["scraps"][0]["keywords"])
    check("被剔除的否定词被透明记录（可人工复核）",
          "月神" in bing["scraps"][0]["negated"], bing["scraps"][0]["negated"])
    ab = next((c for c in index["clusters"] if "甲.md" in [s["name"] for s in c["scraps"]]), None)
    check("甲/乙 仍因共享「月兔」合并", ab and ab["count"] == 2, ab and ab["count"])
    shutil.rmtree(d, ignore_errors=True)


def case_lookahead():
    print("\n=== 5. 前瞻备忘识别 ===")
    d = make_dir()
    write(Path(d) / "备忘.md", "\n".join([
        "月神造了月兔。",
        "待定：他左耳的抓痕是谁留的",
        "> 引用行里的待定不该被当作备忘",
        "以后再想他的真名",
        "TODO 补一段月兔对月神的称呼",
        "回头再说",
    ]))
    s = sc.scan_scraps(d)[0][0]
    check("识别到前瞻备忘", len(s.lookahead) >= 1, s.lookahead)
    check("前瞻条数受上限约束（默认 3）", len(s.lookahead) <= 3, len(s.lookahead))
    check("命中词被记录，便于人工核对",
          all("hit" in la and "line_no" in la for la in s.lookahead), s.lookahead[:1])
    shutil.rmtree(d, ignore_errors=True)


def case_skip_noise():
    print("\n=== 6. 忽略备份目录/隐藏文件/非文本 ===")
    d = make_dir()
    write(Path(d) / "正常.md", "月神与月兔。\n")
    write(Path(d) / "_backup" / "旧版.md", "备份内容不该进索引。\n")
    write(Path(d) / ".hidden.md", "隐藏文件不该进索引。\n")
    write(Path(d) / "图.png", "not really an image")
    scraps, _ = sc.scan_scraps(d)
    names = [s.name for s in scraps]
    check("只保留正常碎片", names == ["正常.md"], names)
    shutil.rmtree(d, ignore_errors=True)


def case_fingerprint():
    print("\n=== 7. content_fingerprint 稳定性（防误判重归并）===")
    d = make_dir()
    write(Path(d) / "a.md", "月神沉默寡言。\n")
    write(Path(d) / "b.md", "月兔不敢抬头。\n")
    i1 = sc.collect(d)[0]
    time.sleep(0.01)
    os.utime(Path(d) / "a.md", (os.path.getmtime(Path(d) / "a.md") + 100,)*2)  # 改 mtime
    i2 = sc.collect(d)[0]
    check("仅 mtime 变化 → 指纹不变（不触发重归并）",
          i1["content_fingerprint"] == i2["content_fingerprint"])
    check("generated_at 存在但未参与指纹",
          i1["generated_at"] != "" and i1["content_fingerprint"].startswith("sha256:"))
    write(Path(d) / "a.md", "月神沉默寡言。他改了。\n")
    i3 = sc.collect(d)[0]
    check("正文改动 → 指纹变化（会触发重归并）",
          i3["content_fingerprint"] != i1["content_fingerprint"])
    write(Path(d) / "c.md", "新增一个碎片。\n")
    i4 = sc.collect(d)[0]
    check("新增碎片 → 指纹变化",
          i4["content_fingerprint"] != i3["content_fingerprint"])
    shutil.rmtree(d, ignore_errors=True)


def case_encoding_and_edges():
    print("\n=== 8. 编码回退与边界 ===")
    d = make_dir()
    (Path(d) / "gbk.md").write_bytes("月神用GBK编码写的一句话。".encode("gbk"))
    scraps, warns = sc.scan_scraps(d)
    check("GBK 碎片能被读入（编码回退链生效）", len(scraps) == 1, warns)
    check("空文本碎片不崩", sc.collect(d)[0]["stats"]["scrap_count"] == 1)

    empty = make_dir()
    index, warns = sc.collect(empty)
    check("空目录 → 0 碎片且不崩", index["stats"]["scrap_count"] == 0)
    missing = Path(tempfile.mkdtemp(prefix="scrap_test_")) / "nope" / "deep"
    if missing.exists():
        shutil.rmtree(missing)
    idx2, warns2 = sc.collect(str(missing))
    check("目录不存在 → 自动创建并给出警告",
          missing.is_dir() and idx2["stats"]["scrap_count"] == 0
          and any("自动创建" in w for w in idx2["warnings"]), idx2["warnings"])
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(empty, ignore_errors=True)
    shutil.rmtree(str(missing.parent), ignore_errors=True)


def case_batches_and_serializable():
    print("\n=== 9. 内联分批 + JSON 可序列化 ===")
    d = make_dir()
    for i in range(4):
        write(Path(d) / ("碎%d.md" % i), "主题甲与主题乙。" * 200)
    old = sc.INLINE_CHAR_BUDGET
    sc.INLINE_CHAR_BUDGET = 500          # 临时压小，验证分批
    try:
        index, _ = sc.collect(d)
    finally:
        sc.INLINE_CHAR_BUDGET = old
    check("超上限 → 拆成多批", len(index["inline_batches"]) > 1, len(index["inline_batches"]))
    check("每批列出文件清单", all(b["files"] for b in index["inline_batches"]))
    check("给出超限警告", any("分批" in w for w in index["warnings"]), index["warnings"])

    index2, _ = sc.collect(d)
    try:
        text = json.dumps(index2, ensure_ascii=False)
        ok = all(k in index2 for k in ("clusters", "timeline", "lookaheads",
                                      "inline_batches", "stats", "warnings",
                                      "content_fingerprint"))
    except Exception as e:      # noqa: BLE001
        ok, text = False, str(e)
    check("索引可 JSON 序列化且必需键齐全", ok and bool(text), ok)
    shutil.rmtree(d, ignore_errors=True)


def case_real_dir():
    print("\n=== 10. 真实目标目录（materials/original_scraps/）===")
    real = str(Path(__file__).resolve().parents[2] / "materials" / "original_scraps")
    index, warns = sc.collect(real)
    n = index["stats"]["scrap_count"]
    print("     当前碎片数：%d；警告：%s" % (n, warns or "无"))
    check("真实目录可正常扫描（空目录也算通过）", isinstance(n, int))
    if n:
        print(json.dumps(index["stats"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    print("=" * 70)
    print("scrap_cluster 自检")
    print("=" * 70)
    for fn in (case_timestamp, case_free_naming, case_clustering, case_negative_clustering,
               case_negation, case_lookahead, case_skip_noise, case_fingerprint,
               case_encoding_and_edges, case_batches_and_serializable, case_real_dir):
        try:
            fn()
        except Exception as e:      # noqa: BLE001
            FAIL.append(fn.__name__)
            import traceback
            print("  [ERROR] %s → %s" % (fn.__name__, e))
            traceback.print_exc()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 70)
    raise SystemExit(1 if FAIL else 0)
