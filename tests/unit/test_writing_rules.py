# -*- coding: utf-8 -*-
"""作品级写作约束（M12）回归：每本书一份，空则不注入。

| 编号 | 钉什么 |
|---|---|
| W1 | 没有文件 → `exists=false` / 注入块为空字符串（**空配置不占每章上下文**） |
| W2 | 保存：落盘 + 回读一致 + 旧版进 history；再存一次有第二份备份 |
| W3 | 超过上限 → **拒收且盘上原文不动**（不静默截断） |
| W4 | 空文本 = 清空：删文件（不留空文件冒充“已配置”），注入块随之变空 |
| W5 | 注入块带标题与正文；外部把文件写超时**截断并标注**（保存路径拒收，外部改拦不住） |
| W6 | 三个写作模板都有 `{{writing_rules}}`，且三处调用方都传了值（漏一处 = 运行时残留占位符） |
| W7 | 路由与转发接线（`/writing_rules` GET/POST） |

用法：python tests/unit/test_writing_rules.py（零 LLM、零网络）
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import writing_rules as wr                          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


# --------------------------------------------------------------------- W1

def case_missing():
    print("\n[W1] 没有文件：exists=false，注入块为空")
    td = Path(tempfile.mkdtemp())
    try:
        info = wr.load(td)
        check("W1 exists=false", info["exists"] is False, info)
        check("W1 text 为空且 chars=0", info["text"] == "" and info["chars"] == 0, info)
        check("W1 注入块是空串（不占上下文）", wr.block(td) == "", repr(wr.block(td))[:60])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- W2

def case_save():
    print("\n[W2] 保存：落盘 + 回读一致 + 旧版进 history")
    td = Path(tempfile.mkdtemp())
    try:
        ok, msg, info = wr.save("必须：第三人称限知\n禁止：现代科技名词\n", td)
        check("W2 保存成功", ok is True, msg)
        p = wr.rules_path(td)
        check("W2 文件已落盘", p.exists(), p)
        check("W2 回读内容一致（去掉首尾空白）",
              p.read_text(encoding="utf-8").strip() == "必须：第三人称限知\n禁止：现代科技名词",
              repr(p.read_text(encoding="utf-8"))[:120])
        check("W2 返回字数", info.get("chars") == len("必须：第三人称限知\n禁止：现代科技名词"),
              info.get("chars"))

        ok2, msg2, info2 = wr.save("必须：只写一件事\n", td)
        hist = sorted((td / "data" / "state" / "writing_rules_history").glob("*.md"))
        check("W2 第二次保存成功", ok2 is True, msg2)
        check("W2 旧版进了 history", len(hist) == 1 and bool(info2.get("backup")), [x.name for x in hist])
        check("W2 备份内容 = 上一版",
              "第三人称限知" in hist[0].read_text(encoding="utf-8"),
              hist[0].read_text(encoding="utf-8")[:40])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- W3

def case_too_long():
    print("\n[W3] 超过上限：拒收且盘上原文不动")
    td = Path(tempfile.mkdtemp())
    try:
        wr.save("短规则", td)
        before = wr.rules_path(td).read_text(encoding="utf-8")
        ok, msg, info = wr.save("长" * (wr.MAX_CHARS + 1), td)
        check("W3 拒收", ok is False, msg)
        check("W3 报错写明上限", str(wr.MAX_CHARS) in msg, msg)
        check("W3 盘上原文一个字没动",
              wr.rules_path(td).read_text(encoding="utf-8") == before)
        check("W3 超长文本不产生新备份",
              len(list((td / "data" / "state" / "writing_rules_history").glob("*.md"))) == 0)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- W4

def case_clear():
    print("\n[W4] 空文本 = 清空（删文件，不留空文件）")
    td = Path(tempfile.mkdtemp())
    try:
        wr.save("该删掉的规则", td)
        ok, msg, info = wr.save("   ", td)
        check("W4 清空成功", ok is True and info.get("cleared") is True, (ok, msg))
        check("W4 文件已删除", not wr.rules_path(td).exists())
        check("W4 注入块回到空串", wr.block(td) == "", repr(wr.block(td)))
        check("W4 旧版仍可从 history 找回",
              any("该删掉的规则" in x.read_text(encoding="utf-8")
                  for x in (td / "data" / "state" / "writing_rules_history").glob("*.md")))
        ok2, msg2, _ = wr.save("", td)
        check("W4 本来就是空 → 也成功（幂等）", ok2 is True, msg2)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- W5

def case_block():
    print("\n[W5] 注入块：带标题；外部写超时截断并标注")
    td = Path(tempfile.mkdtemp())
    try:
        wr.save("必须：每章出现一次具体声音", td)
        b = wr.block(td)
        check("W5 带标题", b.startswith(wr.BLOCK_TITLE), b[:40])
        check("W5 含正文", "每章出现一次具体声音" in b, b[-40:])
        # 外部编辑器绕过 save() 写超长
        wr.rules_path(td).write_text("超" * (wr.MAX_CHARS + 500), encoding="utf-8")
        b2 = wr.block(td)
        check("W5 注入时截断到上限", len(b2) <= wr.MAX_CHARS + 120, len(b2))
        check("W5 截断有明确标注（不装完整）", "已截断" in b2, b2[-60:])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- W6

def case_wiring_prompts():
    print("\n[W6] 三个写作模板 + 三处调用方都接上了")
    for name in ("stage4_writing.md", "stage4_group.md", "stage6_polish.md"):
        p = REPO / "prompts" / name
        txt = p.read_text(encoding="utf-8")
        check("W6 %s 有 {{writing_rules}}" % name, "{{writing_rules}}" in txt,
              [ln for ln in txt.splitlines() if "{{" in ln][:6])
    src4 = (REPO / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    src6 = (REPO / "scripts" / "stage6_polish.py").read_text(encoding="utf-8")
    srcg = (REPO / "scripts" / "utils" / "enhanced_chapter.py").read_text(encoding="utf-8")
    check("W6 stage4_writing 传了 writing_rules", '"writing_rules"' in src4)
    check("W6 stage6_polish 传了 writing_rules", '"writing_rules"' in src6)
    check("W6 加强模式（stage4_group）传了 writing_rules", '"writing_rules": _writing_rules()' in srcg)
    # 前缀纪律：占位符必须落在 body 区（第二个 --- 之后），不许动 frontmatter
    for name in ("stage4_writing.md", "stage4_group.md", "stage6_polish.md"):
        lines = (REPO / "prompts" / name).read_text(encoding="utf-8").splitlines()
        seps = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
        idx = [i for i, ln in enumerate(lines) if "{{writing_rules}}" in ln][0]
        check("W6 %s 的占位符在 body 区（frontmatter 之外）" % name,
              len(seps) >= 2 and idx > seps[1], (seps[:3], idx))


# --------------------------------------------------------------------- W7

def case_routes():
    print("\n[W7] 路由与转发接线")
    from nf_api_domains import project as dom
    routes = {(m, p) for m, p, _ in dom.ROUTES}
    check("W7 GET /writing_rules 已登记", ("GET", "/writing_rules") in routes)
    check("W7 POST /writing_rules 已登记", ("POST", "/writing_rules") in routes)
    api_src = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("W7 nf_api 有 GET 转发", "handle_writing_rules(self)" in api_src)
    check("W7 nf_api 有 POST 转发", "handle_writing_rules_save(self, body)" in api_src)


def case_switch_book():
    print("\n[W8] 换书归档清单包含作品写作约束（否则换书即丢）")
    src = (REPO / "scripts" / "switch_book.py").read_text(encoding="utf-8")
    check("W8 switch_book.ITEMS 含 writing_rules.md",
          "writing_rules.md" in src, [ln for ln in src.splitlines() if "ITEMS" in ln][:3])


def main():
    print("=" * 70)
    print("作品级写作约束（M12）回归")
    print("=" * 70)
    case_missing()
    case_save()
    case_too_long()
    case_clear()
    case_block()
    case_wiring_prompts()
    case_routes()
    case_switch_book()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print("  FAIL: " + name)
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
