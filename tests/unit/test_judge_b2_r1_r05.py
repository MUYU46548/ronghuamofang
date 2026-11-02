# -*- coding: utf-8 -*-
"""B2 整本否掉 / R1 判据失效告警 / R05 引文 / R11 导出合规（2026-10-31）。

用户 2026-10-31 拍板：\u300cB2 加按钮，其他按照计划进行\u300d。四件一起验。

## 每条钉什么

| 编号 | 判据 | 反证（会变红） |
|---|---|---|
| B2 | 理由 <8 字拒收；≥8 字落盘；撤销进 history | 放宽长度校验 → 空理由也能否掉整本 |
| R1 | 命中从 ≥5 掉到 0 且字数达标 → 出声 | 把阈值改成 0 → 任何回落都告警（噪音）；改成 999 → 永不告警（假绿） |
| R05 | 引文只取自正文切句；对不上正文则作废并计数 | 手写一句不存在的引文 → `invalid_refs` 必须 >0 |
| R11 | 导出报告正文里含\u300c含正文原句\u300d提示 | 删掉那句 → 红 |

用法：python tests/unit/test_judge_b2_r1_r05.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.judge import backtest as B          # noqa: E402
from utils.judge import records as R           # noqa: E402

PASS, FAIL = [], []

BODY = """## 第1章 夜雨

他在档案室盖章。他把回执压平，拿起印章，在纸角上按下去。

他核对回执上的编号。他一行一行往下看，看到第三行停住。

他在日志上划改。他把笔尖朝上，放回笔槽。这一句特别长，长到超过六十个字符的上限，
所以按 J03 应当记一条句级缺陷，用来验证引文能不能真的对回正文里去。

他抽出一张字条。他把字条摊在桌上，压在回执底下。
"""

OUTLINE = """- 核心事件：
  1. 他在档案室盖章
  2. 他核对回执编号
  3. 他抽出一张字条
- 涉及角色：沈砚
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def build_root():
    tmp = Path(tempfile.mkdtemp(prefix="nf_b2_"))
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "config").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "B2验收"\n  chapters: 1\n', encoding="utf-8")
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(BODY, encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(OUTLINE, encoding="utf-8")
    return tmp


def case_b2():
    print("\n【B2】整本否掉：理由必填、可撤销、旧记录留档")
    root = build_root()
    ok, err = R.set_book_reject(root, "太平")
    check("理由太短 → 拒收（不是随便点一下就否掉整本）", ok is False,
          err.get("error") if isinstance(err, dict) else err)
    check("拒收时给出可读原因（半年后看得懂）",
          "理由太短" in str(err) and "留档" in str(err), err)
    ok, rec = R.set_book_reject(root, "整本没有冲突也没有推进，重写比改划算")
    check("理由够 → 落盘", ok is True and rec.get("reason"), rec)
    check("记录里有时间与署名", bool(rec.get("at")) and rec.get("by") == "author", rec)
    check("回读一致", (R.get_book_reject(root) or {}).get("reason") == rec["reason"])
    ok2, cleared = R.clear_book_reject(root)
    check("可撤销", ok2 is True and cleared.get("reason") == rec["reason"], cleared)
    check("撤销后当前记录为空", R.get_book_reject(root) is None)
    hist = (R.load(root).get("author") or {}).get("book_reject_history") or []
    check("撤销的记录进 history（曾经否过不能凭空消失）", len(hist) == 1, hist)
    ok3, err3 = R.clear_book_reject(root)
    check("没记录时撤销 → 明确报错而不是静默成功", ok3 is False, err3)
    shutil.rmtree(root, ignore_errors=True)


def case_r1():
    print("\n【R1】判据失效告警：命中骤降 + 字数达标才出声")
    prev = [{"chapter": 1, "counts": {"F01": 4, "F03": 2}}]
    cur = [{"chapter": 1, "counts": {"F01": 0, "F03": 0}, "words": 6000}]
    a = B._detector_alerts(prev, cur, 5000)
    check("4+2=6 ≥5 掉到 0 且字数达标 → 告警", len(a) == 1, a)
    check("文案里写清\u300c可能是真改好了，也可能是判据失效\u300d",
          "真改好" in (a[0]["note"] if a else "") and "判据" in (a[0]["note"] if a else ""), a)
    check("带上前后计数与字数（可核对，不是一句空话）",
          a and a[0]["before"] == 6 and a[0]["after"] == 0 and a[0]["words"] == 6000, a)

    cur2 = [{"chapter": 1, "counts": {"F01": 0, "F03": 0}, "words": 1200}]
    check("字数不达标 → **不**告警（那是稿子变短，不是判据失效）",
          B._detector_alerts(prev, cur2, 5000) == [], cur2)

    prev3 = [{"chapter": 1, "counts": {"F01": 2, "F03": 1}}]
    check("原本就没到 5 → 不告警（本来就没多少可掉的）",
          B._detector_alerts(prev3, cur, 5000) == [], prev3)

    prev4 = [{"chapter": 1}]      # 旧 run 没有 counts
    check("旧历史 run 没有分规则计数 → 跳过，不假报",
          B._detector_alerts(prev4, cur, 5000) == [], prev4)

    prev5 = [{"chapter": 2, "counts": {"F01": 9, "F03": 0}}]
    cur5 = [{"chapter": 1, "counts": {"F01": 0, "F03": 0}, "words": 6000}]
    check("章号对不上 → 不告警（别拿第 1 章比第 2 章）",
          B._detector_alerts(prev5, cur5, 5000) == [], cur5)


def case_r05():
    print("\n【R05】判据引文：只取自正文切句，对不上就作废并计数")
    root = build_root()
    res = B.judge_chapter(root, 1)
    refs, invalid = B._rule_refs(res)
    check("能从一次判定里取到引文", bool(refs), list(refs))
    body = res.get("body") or ""
    bad = [rid for rid, r in refs.items()
           if (r.get("sentence") or "").rstrip("…") not in body]
    check("每条引文都能在正文里找到（AC-05-2）", not bad, bad)
    check("引文带位置（第几段），读者能翻回去", all(
        r.get("paragraph_index") is not None for r in refs.values()), refs)
    check("正常稿子的作废计数为 0", invalid == 0, invalid)

    # 反证：伪造一条正文里没有的引文 → 必须被丢掉并计数
    import copy
    fake = copy.deepcopy(res)
    fake["analysis"]["defects"].append({
        "rule_id": "J99", "sentence": "这句话在正文里根本不存在", "paragraph_index": 9,
        "rule_name": "伪造"})
    refs2, invalid2 = B._rule_refs(fake)
    check("对不上正文的引文被丢弃且计数 +1",
          "J99" not in refs2 and invalid2 == 1, (list(refs2), invalid2))
    shutil.rmtree(root, ignore_errors=True)


def case_r11():
    print("\n【R11】导出报告必须自带\u300c含正文原句\u300d提示")
    root = build_root()
    rep = B.run(root, book_name="B2验收")
    check("回测跑通", rep.get("ok") is True, rep.get("error"))
    out = B.export_markdown(rep, root)
    text = out.read_text(encoding="utf-8")
    check("报告正文里有合规提示", "含正文原句" in text, text[:200])
    check("说清了后果（等于外发一部分稿件）", "外发" in text or "转给他人" in text, "")
    check("报告里带判据引文小节（有命中才有）",
          ("判据引文" in text) if rep.get("refs") else True,
          [k for k in (rep.get("refs") or {})])
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 66)
    print("B2 / R1 / R05 / R11")
    print("=" * 66)
    case_b2()
    case_r1()
    case_r05()
    case_r11()
    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
