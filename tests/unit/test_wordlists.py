# -*- coding: utf-8 -*-
"""词表（病灶词 / 合规词）的回归锁（B1，2026-11-06）。

## 这个文件锁什么

词表此前只有“改配置文件”一条路，于是有两种静默失真：

1. **界面改了不生效**：`cliche_scan` 只认 `quality.cliche_wordlist`，
   GUI 写的 `data/wordlists/cliche.txt` 根本不参与 —— 作者改完看到“零命中”，
   以为是稿子干净了；
2. **坏表静默变空**：空表 / 全是注释 / 正则编译失败时，旧实现 `load_wordlist`
   抛 ValueError，上层 `except` 后回落默认，但**没有一处把这件事告诉人**。

判据（对应 task-20261105-001 的验收线）：

- 保存一份 3 词病灶表 → `load_cliche` / `scan_text` 按**界面词表**（source=gui）命中；
- 清空表 → 回落内置 20 条**且带 warning**（不许静默变空）；
- 合规词：没文件 = 未配置（静默）；有文件 = 只报告（info），不阻断；
- 写入校验：未知字段 / 超长行 / 控制字符一律 400，配置文件与词表文件都不脏。

用法：python tests/unit/test_wordlists.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


from nf_api_domains import project as dom_proj                  # noqa: E402
from utils import wordlists as wl                              # noqa: E402


class _FakeH:
    headers = {}


def _tmp():
    return Path(tempfile.mkdtemp(prefix="nf_wl_"))


def _with_root(tmp_root, fn, *a, **kw):
    """把 `nf_api.ROOT` 指到临时根再调 handler（handler 用的是模块级 ROOT，不是 CWD）。"""
    import nf_api
    old = nf_api.ROOT
    nf_api.ROOT = tmp_root
    try:
        return fn(*a, **kw)
    finally:
        nf_api.ROOT = old


def case_gui_wordlist_takes_effect():
    print("\n【1】保存 3 词病灶表 → 扫描按界面词表命中（source=gui）")
    tmp = _tmp()
    body = {"cliche": "# 我的表\n微微一笑\n眸光\nregex:仿佛[^，。！？\\n]{0,12}一般\n"}
    code, payload = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(), body)
    check("保存 200", code == 200, (code, str(payload)[:160]))
    p = tmp / wl.CLICHE_REL
    check("落盘位置固定 data/wordlists/cliche.txt", p.exists())
    check("盘上真的是 3 条（注释/空行不算）",
          len([l for l in p.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.strip().startswith("#")]) == 3)
    info = wl.load_cliche(tmp)
    check("load_cliche 生效档位 = gui", info["source"] == "gui", info["source"])
    check("解析出 3 条", len(info["terms"]) == 3, len(info["terms"]))

    # 真扫一次：正文里放两个命中词
    from utils.cliche_scan import scan_text
    text = "她微微一笑，眸光微动。他仿佛看见了什么一般。"
    rep = scan_text(text, root=str(tmp))
    check("scan_text 报明用了界面词表", rep.get("wordlist_source") == "gui",
          rep.get("wordlist_source"))
    check("扫描真的命中（≥3 次）", int(rep.get("总命中次数") or 0) >= 3,
          rep.get("总命中次数"))
    hits = dict(rep.get("排行") or [])
    check("命中词是界面表里的词（微微一笑 / 眸光 / 正则模式之一）",
          any(k in hits for k in ("微微一笑", "眸光")) or "仿佛" in str(rep.get("排行")),
          rep.get("排行"))


def case_empty_wordlist_falls_back_loudly():
    print("\n【2】清空词表 → 回落内置 20 条，且必须带 warning（不许静默变空）")
    tmp = _tmp()
    code, _ = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(), {"cliche": ""})
    check("清空也能保存（= 明确“不用自定义表”）", code == 200, code)
    info = wl.load_cliche(tmp)
    check("回落到内置表", info["source"] == "builtin", info["source"])
    check("内置 20 条", len(info["terms"]) == 20, len(info["terms"]))
    check("带 warning（说清为什么回落）", "回落" in (info["warning"] or ""), info["warning"])

    code, payload = _with_root(tmp, dom_proj.handle_config_wordlists, _FakeH())
    check("GET 回显 warning（界面看得见）",
          code == 200 and (payload.get("cliche") or {}).get("warning"), str(payload)[:160])
    check("GET 回显生效档位 = 内置", (payload.get("cliche") or {}).get("source") == "builtin",
          (payload.get("cliche") or {}).get("source"))


def case_bad_lines_are_skipped_not_fatal():
    print("\n【3】坏行（正则编译失败）只跳过自己，整表不作废，并记 warning")
    tmp = _tmp()
    text = "微微一笑\nregex:([未闭合\n眸光\n"
    code, _ = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(), {"cliche": text})
    check("保存 200（坏行不该 400 整张表）", code == 200, code)
    info = wl.load_cliche(tmp)
    check("好行仍然生效（2 条）", len(info["terms"]) == 2, [t.name for t in info["terms"]])
    check("坏行有 warning 且带行号", "第 2 行" in (info["warning"] or ""), info["warning"])


def case_write_validation():
    print("\n【4】写入校验：未知字段 / 超长行 / 控制字符 → 400，盘上不脏")
    tmp = _tmp()
    code, payload = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(),
                               {"cliche": "a", "bogus": "x"})
    check("未知字段 400", code == 400 and "未知字段" in str(payload.get("error")), payload)
    code, payload = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(), {})
    check("什么都不传 400", code == 400, payload)
    code, payload = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(),
                               {"cliche": "x" * (wl.MAX_LINE_CHARS + 1)})
    check("超长单行 400", code == 400 and "超过上限" in str(payload.get("error")), payload)
    code, payload = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(),
                               {"cliche": "好词\x00坏"})
    check("控制字符 400", code == 400 and "控制字符" in str(payload.get("error")), payload)
    check("被拒的写入没有留下文件", not (tmp / wl.CLICHE_REL).exists())
    code, payload = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(),
                               {"cliche": "正则\nregex:[坏\n"})
    check("正则坏行不算“参数非法”（保存成功、跳过该行）", code == 200, (code, payload))


def case_compliance_channel():
    print("\n【5】合规词：没文件=未配置（静默）；有文件=只报告")
    tmp = _tmp()
    info = wl.load_compliance(tmp)
    check("没文件：source 空、无 warning（未配置 ≠ 故障）",
          info["source"] == "" and info["warning"] == "", info)
    code, _ = _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(),
                         {"compliance": "禁词一\n禁词二\n"})
    check("保存合规表 200", code == 200, code)
    info = wl.load_compliance(tmp)
    check("生效 2 条", len(info["terms"]) == 2, info["terms"])
    hits = wl.compliance_hits("这里出现了禁词一，还有禁词一和禁词二。", info["terms"])
    check("命中统计正确（禁词一 2 次）",
          hits and hits[0]["name"] == "禁词一" and hits[0]["count"] == 2, hits)

    sys.path.insert(0, str(ROOT / "scripts"))
    import proofread
    findings = proofread.check_compliance("这里出现了禁词一。", 3, info["terms"])
    check("校对产出 info 级 finding（不阻断）",
          findings and all(f["severity"] == "info" for f in findings), findings)
    check("finding 里带原句（人看得懂上下文）",
          findings and "禁词一" in findings[0].get("location", ""), findings[:1])


def case_describe_shape():
    print("\n【6】GET 结构：原文 + 档位 + 条数 + 路径（界面靠它渲染）")
    tmp = _tmp()
    _with_root(tmp, dom_proj.handle_config_wordlists_set, _FakeH(), {"cliche": "一滴\n"})
    code, d = _with_root(tmp, dom_proj.handle_config_wordlists, _FakeH())
    check("GET 200", code == 200)
    check("两份表都在（cliche / compliance）",
          isinstance(d.get("cliche"), dict) and isinstance(d.get("compliance"), dict))
    check("回的是原文（界面要能编辑）", "一滴" in (d.get("cliche") or {}).get("text", ""))
    check("带合法上限（界面不自己猜）",
          (d.get("limits") or {}).get("max_line_chars") == wl.MAX_LINE_CHARS, d.get("limits"))


def main():
    case_gui_wordlist_takes_effect()
    case_empty_wordlist_falls_back_loudly()
    case_bad_lines_are_skipped_not_fatal()
    case_write_validation()
    case_compliance_channel()
    case_describe_shape()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
