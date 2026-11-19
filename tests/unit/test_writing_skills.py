# -*- coding: utf-8 -*-
"""写作 Skill 导入（2026-11-19，作者拍板：只留用户导入，不自创文风）。

| 编号 | 钉什么 |
|---|---|
| S1 | 没有 `materials/skills/` → 清单为空、注入空串（不占上下文） |
| S2 | 新建后**未确认 → 不注入**（看完才生效） |
| S3 | 确认后注入，块里带标题与正文 |
| S4 | 确认后改了文件（sha 变）→ 自动失效，重新变成未确认 |
| S5 | 目录里有 `.py` → 整份拒装且点名文件（永不执行导入物） |
| S6 | 缺 `SKILL.md` → 问题写明文件名要求 |
| S7 | 单份超上限 → 拒收并给出字数（不截断） |
| S8 | 合计超预算 → 后一份不注入，块尾**写明有几套没注入**（不静默） |
| S9 | 只绑 `revise` 的不会进 `write`（按阶段绑定） |
| S10 | 停用 → 一个字都不注入 |
| S11 | 删除是移到回收目录（原目录消失、回收里有），不是 rm |
| S12 | 非法 id 拒收（含空格 / 斜杠 / ..） |
| S13 | frontmatter 可选：没写也能用（默认 write），写了就按它 |
| S14 | stages 写错（如 plan）→ 拒装并列出可接受值 |
| S15 | 接线：6 条路由登记 + stage4/stage6 都调了注入函数 |
| S16 | 改绑定走 state，**不动作者的原文件**（改回读一致） |

用法：python tests/unit/test_writing_skills.py（零 LLM、零网络）
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import writing_skills as ws                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)[:220]) if extra else ""))


def _put(root, sid, text, front=None):
    d = ws.skills_dir(root) / sid
    d.mkdir(parents=True, exist_ok=True)
    body = text
    if front:
        body = "---\n" + "\n".join(front) + "\n---\n\n" + text
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        print("\n【S1】没有目录 → 空清单、空注入")
        check("scan 空", ws.scan(root) == [])
        text, st = ws.block(("write",), root)
        check("block 空串", text == "", text)
        check("stats 无注入", st["injected"] == [], st)

        print("\n【S2】新建但未确认 → 不注入")
        ok, msg = ws.create("cold-tone", "少用形容词。句子短。", root, name="冷峻")
        check("新建成功", ok, msg)
        check("回读字数对", (ws.get("cold-tone", root) or {}).get("chars") == 10,
              (ws.get("cold-tone", root) or {}).get("chars"))
        check("未确认标记", ws.get("cold-tone", root)["approved"] is False)
        text, st = ws.block(("write",), root)
        check("未确认不注入", text == "", text)

        print("\n【S3】确认后注入")
        ok, msg = ws.approve("cold-tone", root)
        check("确认成功", ok, msg)
        text, st = ws.block(("write",), root)
        check("有注入", st["injected"] == ["cold-tone"], st)
        check("带标题", ws.BLOCK_TITLE in text, text[:60])
        check("带正文", "少用形容词" in text, text)
        check("带名字", "### 冷峻" in text, text)

        print("\n【S4】确认后改文件 → 自动失效")
        p = ws.skills_dir(root) / "cold-tone" / "SKILL.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n再加一条。", encoding="utf-8")
        e = ws.get("cold-tone", root)
        check("approved 变 false", e["approved"] is False, e["sha"])
        check("问题说明被改过", any("改过" in x for x in e["problems"]), e["problems"])
        text, _ = ws.block(("write",), root)
        check("改后不注入", text == "", text)
        ws.approve("cold-tone", root)   # 复原，后续用例要一份可用的

        print("\n【S5】目录里有脚本 → 整份拒装")
        _put(root, "evil", "正文", front=["name: 坏东西", "stages: [write]"])
        (ws.skills_dir(root) / "evil" / "run.py").write_text("print(1)", encoding="utf-8")
        e = ws.get("evil", root)
        check("点名脚本", any("run.py" in x for x in e["problems"]), e["problems"])
        check("说明只收纯文本", any("纯文本" in x for x in e["problems"]), e["problems"])
        text, st = ws.block(("write",), root)
        check("拒装的不注入", "evil" not in st["injected"], st)
        check("但它进了 skipped（出声）", any(s["id"] == "evil" for s in st["skipped"]), st)

        print("\n【S6】缺 SKILL.md")
        d = ws.skills_dir(root) / "empty-dir"
        d.mkdir(parents=True, exist_ok=True)
        (d / "notes.md").write_text("随手记", encoding="utf-8")
        e = ws.get("empty-dir", root)
        check("问题写文件名", any("SKILL.md" in x for x in e["problems"]), e["problems"])

        print("\n【S7】单份超上限")
        big = "字" * (ws.MAX_SKILL_CHARS + 1)
        ok, msg = ws.create("too-long", big, root)
        check("create 拒收", ok is False, msg)
        check("说明上限", str(ws.MAX_SKILL_CHARS) in msg, msg)
        _put(root, "too-long2", "字" * (ws.MAX_SKILL_CHARS + 50))
        e = ws.get("too-long2", root)
        check("外部放进去也标问题", any("超过单份上限" in x for x in e["problems"]),
              e["problems"])

        print("\n【S8】合计超预算 → 后者不注入且出声")
        ws.create("a-half", "甲" * int(ws.MAX_TOTAL_CHARS * 0.6), root)
        ws.create("b-half", "乙" * int(ws.MAX_TOTAL_CHARS * 0.6), root)
        ws.approve("a-half", root)
        ws.approve("b-half", root)
        text, st = ws.block(("write",), root)
        picked = [x for x in st["injected"] if x in ("a-half", "b-half")]
        check("两份只进一份", len(picked) == 1, st)
        check("另一份进 skipped", any(s["id"] in ("a-half", "b-half")
                                      for s in st["skipped"]), st)
        check("块尾写明未注入", "未注入" in text, text[-80:])
        # 清场，后续用例要干净基线
        ws.remove("a-half", root)
        ws.remove("b-half", root)

        print("\n【S9】按阶段绑定")
        ws.create("rev-only", "润色时才用的规矩", root, stages=["revise"])
        ws.approve("rev-only", root)
        _, st = ws.block(("write",), root)
        check("不进 write", "rev-only" not in st["injected"], st)
        _, st = ws.block(("revise",), root)
        check("进 revise", "rev-only" in st["injected"], st)

        print("\n【S10】停用")
        ok, _ = ws.set_binding("rev-only", enabled=False, root=root)
        check("停用成功", ok)
        _, st = ws.block(("revise",), root)
        check("停用后不注入", "rev-only" not in st["injected"], st)
        ws.set_binding("rev-only", enabled=True, root=root)

        print("\n【S11】删除 = 移到回收")
        ok, msg = ws.remove("rev-only", root)
        check("删除返回成功", ok, msg)
        check("原目录没了", not (ws.skills_dir(root) / "rev-only").exists())
        tr = list((root / "data" / "state" / "skills_trash").glob("rev-only_*"))
        check("回收里有一份", len(tr) == 1, tr)
        check("清单里也没了", all(e["id"] != "rev-only" for e in ws.scan(root)))
        check("提示写明不是删除", "不是删除" in msg, msg)

        print("\n【S12】非法 id")
        for bad in ("a b", "a/b", "..", ""):
            ok, msg = ws.create(bad, "x", root)
            check("拒收 %r" % bad, ok is False, msg)
        check("valid_id 放行中文", ws.valid_id("冷峻笔法") is True)

        print("\n【S13】frontmatter 可选")
        _put(root, "no-front", "没有任何 frontmatter 的正文。")
        e = ws.get("no-front", root)
        check("默认 stages=write", e["stages"] == ["write"], e["stages"])
        check("正文完整", e["text"] == "没有任何 frontmatter 的正文。", e["text"])
        _put(root, "with-front", "正文在此。",
             front=["name: 自定义名", "stages: [write, revise]", "enabled: false"])
        e = ws.get("with-front", root)
        check("读到 name", e["name"] == "自定义名", e["name"])
        check("读到 stages", e["stages"] == ["write", "revise"], e["stages"])
        check("读到 enabled=false", e["enabled"] is False, e["enabled"])
        check("frontmatter 不进正文", "---" not in e["text"], e["text"])

        print("\n【S14】stages 写错")
        ok, msg = ws.create("bad-stage", "x", root, stages=["plan"])
        check("拒收", ok is False, msg)
        check("列出可接受值", "write" in msg and "revise" in msg, msg)
        _put(root, "bad-stage", "x", front=["stages: [plan]"])
        e = ws.get("bad-stage", root)
        check("外部放进来的也标问题", any("不认识的阶段" in x for x in e["problems"]),
              e["problems"])

        print("\n【S15】绑定走 state，不动原文件")
        before = (ws.skills_dir(root) / "cold-tone" / "SKILL.md").read_text(encoding="utf-8")
        ok, _ = ws.set_binding("cold-tone", stages=["write", "revise"], root=root)
        after = (ws.skills_dir(root) / "cold-tone" / "SKILL.md").read_text(encoding="utf-8")
        check("原文件没被改", before == after)
        ok, msg = ws.set_binding("cold-tone", stages=[], root=root)
        check("空 stages 拒收", ok is False, msg)
        ok, msg = ws.set_binding("cold-tone", stages=["plan"], root=root)
        check("坏 stages 拒收", ok is False, msg)
        ok, msg = ws.set_binding("不存在的", enabled=True, root=root)
        check("不存在的 id 拒收", ok is False, msg)

        print("\n【S16】接线")
        api = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        for path in ("/skills", "/skills/detail", "/skills/create",
                     "/skills/approve", "/skills/binding", "/skills/delete"):
            check("路由 %s" % path, path in api)
        dom = (REPO / "scripts" / "nf_api_domains" / "project.py").read_text(encoding="utf-8")
        for fn in ("handle_skills", "handle_skill_detail", "handle_skills_create",
                   "handle_skills_approve", "handle_skills_binding", "handle_skills_delete"):
            check("处理器 %s" % fn, fn in dom)
        s4 = (REPO / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
        check("stage4 调了注入", '_writing_skills_block("write")' in s4)
        s6 = (REPO / "scripts" / "stage6_polish.py").read_text(encoding="utf-8")
        check("stage6 调了注入", '_writing_skills_block("revise")' in s6)
        check("预算常量", ws.MAX_SKILL_CHARS > 0 and ws.MAX_TOTAL_CHARS > 0)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
