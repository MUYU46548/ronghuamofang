# -*- coding: utf-8 -*-
"""真人作品拆解（P2 内核）的自检。

## 主判据只有两条

1. **画像算得对**：3 章文本 → 章数、章长、对话占比、钩子率都落在可解释的范围。
2. **不存原文**：输出 JSON 里不许出现任何一句原文。这条是版权底线，
   不是"最好没有"——所以断言用原文里的独特句子去搜，搜到即失败。

另钉三个坑：
- 正文里单独一行"第三章"（间隔不足 300 字）**不许**被当成章标题拆走；
- 无章节标题的整篇 → 当成 1 章，而不是报错；
- 有效正文 <1000 字 → 明确拒绝（算出来的画像会误导写作）。

用法：python tests/unit/test_style_profile.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import style_profile as SP                            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  <- " + str(detail)) if (detail and not cond) else ""))


BODY = """他推开门，屋里没有人，桌上的茶还热着。他把外套挂在门后，走到桌前坐下。
茶碗边上有一道裂纹，被摩过很多次，边缘已经发亮。他端起茶喝了一口，放下，又端起来。
“你回来晚了。”窗边有人说话。他没有回头，只是把茶碗推过去一点。
那人走过来，坐下，手指在桌面上敲了三下。这是他们二十年前的暗号，意思是有事要说。
屋子里很暗，只有炉膛里的火还在响。火光把两个人的影子投在墙上，一高一低。
“东西还在吗。”他问。对方没有答，从怀里摸出一个布包，放在桌上，推过来。
布包很轻，落在桌面上没有声音。他伸手去解，结打得很紧，解了三次才开。
里面是一块表。表壳上有一道旧划痕，秒针还在走，声音很轻，但在安静的屋里听得清楚。
他看了很久，把表翻过来。表盖内侧刻着两个字，被磨得很浅，但还认得出来。
“你从哪弄来的。”他问。对方说了一个地名，他没有接话。
窗外的风把门吹开一条缝，火光晃了一下。他起身去关门，回来时布包已经不见了。
桌上只剩那块表，还有一张对折的纸。纸上只有一个日期，比档案里记的早一天。
他把表收进兜里，把纸折好，放进内袋。然后他坐下，把茶喝完，一句话也没有再说。"""

BODY2 = BODY.replace("他推开门", "天还没亮他就出了门").replace(
    "窗边有人说话", "院墙外有人在等").replace("布包", "油纸包")

BODY3 = BODY.replace("他推开门", "第二天落雪了他才回来").replace(
    "茶碗边上", "门槛边上").replace("表壳上", "表盘上")


def build_text():
    return "\n".join([
        "第一章 归途", "", BODY, "",
        # 陷阱：正文里单独一行"第三章"，但它离「第二章」标题只有几十字
        # （真实场景：作者在开头引用了"第三章提到的那件事"）
        "第二章 旧物", "", "他又把这句想过一遍。", "第三章", "那句话终究没有说出口。", "",
        BODY2, "",
        "第四章 落雪", "", BODY3, "",
        "第五章 尾声", "", BODY, "",
    ])


def main():
    print("== 拆解内核 ==")
    tmp = Path(tempfile.mkdtemp(prefix="nf_profile_"))
    try:
        src = tmp / "sample_novel.txt"
        text = build_text()
        src.write_text(text, encoding="utf-8")

        # ---- 1. 拆章 ----
        print("\n[1] 拆章")
        preface, chapters = SP.split_chapters(text)
        titles = [t for t, _ in chapters]
        check("拆出 4 章（陷阱行不算）", len(chapters) == 4, titles)
        check("第一章标题正确", titles and "第一章" in titles[0], titles)
        check("陷阱的「第三章」没被拆出来",
              sum(1 for t in titles if t.strip() == "第三章") == 0, titles)
        check("前言为空（正文从第一章开始）", preface == "", preface[:40])

        # ---- 2. 无标题整篇 ----
        print("\n[2] 无标题整篇")
        pre2, ch2 = SP.split_chapters(BODY)
        check("整篇当成一章", len(ch2) == 1, len(ch2))
        check("标题为「全文」", ch2[0][0] == "全文", ch2[0][0])

        # ---- 3. 画像 ----
        print("\n[3] 画像")
        prof = SP.build_profile(chapters, source_name=src.name, source_text=text)
        s = prof["summary"]
        check("章数 4", s["chapter_count"] == 4, s["chapter_count"])
        check("全部章可用（样本足够）", s["usable_chapters"] == 4, s["usable_chapters"])
        check("总字数 > 1500", s["total_chars"] > 1500, s["total_chars"])
        check("章长中位数 > 300", s["chapter_len"]["median"] > 300, s["chapter_len"])
        check("对话占比 > 0.02（比率不许被四舍五入成 0）",
              s["dialogue_ratio"]["mean"] > 0.02, s["dialogue_ratio"])
        check("句长均值 5-40", 5 < s["avg_sent_len"]["mean"] < 40, s["avg_sent_len"])
        check("钩子率 0-1", 0 <= s["hook_rate"] <= 1, s["hook_rate"])
        check("guidance 有内容", len(prof["guidance"]) >= 3, prof["guidance"])
        check("guidance 提到章长", any("章长" in g for g in prof["guidance"]), prof["guidance"])

        # ---- 4. 不存原文（版权底线）----
        print("\n[4] 不存原文")
        blob = json.dumps(prof, ensure_ascii=False)
        for phrase in ("这道裂纹", "布包很轻", "二十年前的暗号", "表壳上有一道旧划痕"):
            check("输出不含原句「%s」" % phrase, phrase not in blob, "命中了原文")
        check("只留文件名", prof["source"]["name"] == src.name, prof["source"])
        check("留哈希便于核对", len(prof["source"]["sha256_8"]) == 8, prof["source"])
        check("notice 写明不存原文", "不含原文" in prof["notice"], prof["notice"])

        # ---- 6. 片段勾选 ----
        print("\n[6] 片段候选")
        dlg = ("“你回来晚了。”窗边有人说话。他没有回头，只是把茶碗推过去一点。"
               "“东西还在吗。”他问。对方没有答。") * 3
        act = ("他推开门，走进去，站在桌边，拿起茶碗，放下，转身，跨过门槛，"
               "又退回来，伸手把灯拨亮。") * 3
        scn = ("屋子里很暗，只有炉膛里的火还在响，光落在墙上，影子一高一低，"
               "冷风从门缝里进来。") * 3
        sample = "\n\n".join([dlg, act, scn])
        snips = SP.extract_snippets(sample, per_kind=2)
        kinds = [s["kind"] for s in snips]
        check("抽到候选", len(snips) >= 3, len(snips))
        check("每段长度合规", all(80 <= s["chars"] <= 400 for s in snips),
              [(s["kind"], s["chars"]) for s in snips])
        check("识别出对话段", "dialogue" in kinds, kinds)
        check("识别出动作段", "action" in kinds, kinds)
        check("识别出描写段", "scene" in kinds, kinds)
        check("候选带 id 与段落序号", all(s["id"] and isinstance(s["para_index"], int)
                                       for s in snips))

        # ---- 7. 声明门禁 ----
        print("\n[7] 声明门禁")
        ok, msg = SP.save_snippets(snips[:1], root=str(tmp))
        check("未同意时拒存", not ok and "声明" in msg, msg)
        check("拒存后没有文件", not (tmp / "materials" / "style_snippets.json").exists())

        SP.set_consent(str(tmp), True)
        check("同意状态可读回", SP.consent_state(str(tmp))["agreed"] is True)
        ok, msg = SP.save_snippets(snips[:2], root=str(tmp))
        check("同意后可存", ok, msg)
        check("片段文件写出", (tmp / "materials" / "style_snippets.json").exists())
        check("只存勾选的两条", len(SP.load_snippets(str(tmp))["snippets"]) == 2)

        ok, msg = SP.save_snippets(snips * 3, root=str(tmp))
        check("超过 6 条拒绝", not ok and "6" in msg, msg)
        ok, msg = SP.save_snippets([{"kind": "x", "text": "太短"}], root=str(tmp))
        check("过短片段拒绝", not ok, msg)

        # ---- 8. stage4 注入块 ----
        print("\n[8] stage4 注入块")
        block = SP.load_snippets_block(str(tmp))
        check("块含标题", "参考片段" in block, block[:60])
        check("块含「不要复制」警告", "不要复制" in block)
        check("块含片段正文", snips[0]["text"][:12] in block)
        SP.clear_snippets(str(tmp))
        check("清空后块为空", SP.load_snippets_block(str(tmp)) == "")

        # ---- 9. 候选端点（只读、不落盘）----
        print("\n[9] 候选端点")
        import nf_api
        from nf_api_domains import runtime as dom_rt
        novel = tmp / "novel.txt"
        novel.write_text(sample, encoding="utf-8")
        old_root = nf_api.ROOT
        try:
            nf_api.ROOT = tmp
            code, d = dom_rt.handle_decompose_candidates(None, {"path": str(novel)})
            check("候选返回 200", code == 200 and d["ok"], d)
            check("候选不落盘", not (tmp / "materials" / "style_snippets.json").exists())
            check("候选带 note 说明未落盘", "未写入磁盘" in (d.get("note") or ""), d.get("note"))
            code, d = dom_rt.handle_decompose_candidates(None, {"path": str(tmp / "nope")})
            check("文件不存在 404", code == 404, d)
            code, d = dom_rt.handle_decompose_candidates(None, {})
            check("缺 path 400", code == 400, d)
        finally:
            nf_api.ROOT = old_root

        # ---- 5. 落盘与拒绝 ----
        print("\n[5] 落盘与边界")
        ok, msg, prof2 = SP.decompose(str(src), out_path=str(tmp / "materials" / "style_profile.json"))
        check("decompose 成功", ok, msg)
        check("画像文件写出", (tmp / "materials" / "style_profile.json").exists())
        check("load_profile 可读回", SP.load_profile(str(tmp)) is not None)

        short = tmp / "short.txt"
        short.write_text("太短的一篇。" * 20, encoding="utf-8")
        ok, msg, _ = SP.decompose(str(short), out_path=str(tmp / "x.json"))
        check("正文过短被拒绝", not ok and "1000" in msg, msg)

        ok, msg, _ = SP.decompose(str(tmp / "nope.txt"), out_path=str(tmp / "x.json"))
        check("文件不存在被拒绝", not ok and "不存在" in msg, msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
