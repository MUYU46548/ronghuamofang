# -*- coding: utf-8 -*-
"""产物速览 = 盘上事实（2026-11-04，试跑第 13/20 条）。

## 要治的病

旧实现是前端**拼**出来的五个固定相对路径，只有 Word 那行查了“最新 docx”：

- 设定集 / 素材体检 / 整体大纲 / 素材清单 → 从不看盘，文件不存在照样给
  “预览 / 打开”，点了报错（作者原话：“经典打不开素材体检报告，文件不存在”）；
- Word 行 → 路径按 `output/{书名}_完整版.docx` 拼，而“存不存在”问的是
  `output/` 里最新的那个 docx —— 换书后**上一本书的成品**还在，于是按钮可点、
  路径不存在（作者第 20 条，第二次提；第 22 条“章节名还是上本书的第9章划号”
  也是同一条根因：顶栏“成品”打开的就是上一本书）。

## 判据（每条都能变红）

- A1 空工作区 → 五项全部 `exists=False`，Word 行给的是“这本书该有的路径”而不是别人的
- A2 只有部分产物 → 只有那几项 `exists=True`
- A3 output/ 里同时有本书与别书的 docx → Word 只认**本书**那份（路径用盘上真实文件名）
- A4 output/ 里只有别书的 docx → Word `exists=False`（绝不拿别的书顶替）
- A5 本书 docx 有多份 → 取最新那份
- A6 接线：`/state` 的 payload 里带 `artifacts`；前端读它，不再自己拼

用法：python tests/unit/test_artifact_truth.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api                                                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (detail,)))


def _touch(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _by_key(items):
    return {it["key"]: it for it in items}


def main():
    print("=" * 66)
    print("产物速览 = 盘上事实")
    saved_root = nf_api.ROOT
    tmp = Path(tempfile.mkdtemp())
    nf_api.ROOT = tmp
    try:
        print("\n【A1】空工作区：五项全标尚未产出")
        items = _by_key(nf_api._artifact_paths("测试用书1104"))
        check("A1 五个键都在", set(items) == {
            "setting", "material_review", "outline", "manifest", "word"}, set(items))
        check("A1 全部 exists=False",
              not any(it["exists"] for it in items.values()), items)
        check("A1 Word 行指向本书路径（尚无此文件，但路径有意义）",
              items["word"]["path"] == "output/测试用书1104_完整版.docx",
              items["word"]["path"])

        print("\n【A2】部分产物存在 → 只有它们亮")
        _touch(tmp / "data" / "setting" / "setting.json", "{}")
        _touch(tmp / "data" / "outline" / "global.md", "# 大纲")
        items = _by_key(nf_api._artifact_paths("测试用书1104"))
        check("A2 设定集亮", items["setting"]["exists"] is True)
        check("A2 整体大纲亮", items["outline"]["exists"] is True)
        check("A2 素材体检仍暗（文件真的不存在）",
              items["material_review"]["exists"] is False)
        check("A2 素材清单仍暗", items["manifest"]["exists"] is False)

        print("\n【A3】output/ 里有别书的 docx → 不算本书的")
        _touch(tmp / "output" / "潮汐档案_完整版.docx", "old")
        items = _by_key(nf_api._artifact_paths("测试用书1104"))
        check("A3 别书 docx 不冒充本书成品",
              items["word"]["exists"] is False, items["word"])

        print("\n【A4】本书 docx 出现 → 路径用盘上真实文件名")
        _touch(tmp / "output" / "测试用书1104_完整版.docx", "new")
        items = _by_key(nf_api._artifact_paths("测试用书1104"))
        check("A4 亮起来", items["word"]["exists"] is True)
        check("A4 路径 = 真实文件名",
              items["word"]["path"] == "output/测试用书1104_完整版.docx",
              items["word"]["path"])

        print("\n【A5】本书有多份成品 → 取最新那份")
        import os
        import time
        old = tmp / "output" / "测试用书1104_完整版.docx"
        new = tmp / "output" / "测试用书1104_v2.docx"
        _touch(new, "v2")
        time.sleep(0.05)
        os.utime(old, None)          # 把“完整版”刷成更新
        items = _by_key(nf_api._artifact_paths("测试用书1104"))
        check("A5 取 mtime 最新的那份",
              items["word"]["path"] == "output/测试用书1104_完整版.docx",
              items["word"]["path"])
        os.utime(new, None)
        time.sleep(0.05)
        os.utime(new, None)
        items = _by_key(nf_api._artifact_paths("测试用书1104"))
        check("A5 换一份更新的 → 跟着换",
              items["word"]["path"] == "output/测试用书1104_v2.docx",
              items["word"]["path"])

        print("\n【A6】接线：/state 带 artifacts，前端读它而不是自己拼")
        src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        check("A6 build_state 返回 artifacts",
              '"artifacts": _artifact_paths(' in src)
        app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
        check("A6 前端读 state.artifacts",
              "state.value?.artifacts" in app)
        check("A6 前端按 exists 判按钮（不再用 missing / 自己拼 latestDocx）",
              "!a.exists" in app and 'v-if="a.missing"' not in app)
        check("A6 latestDocx 与产物速览同源（只认本书成品）",
              "a.key === \"word\" && a.exists" in app)

        # 形状契约
        print("\n【A7】形状契约：JSON 可序列化、字段齐全")
        payload = json.loads(json.dumps(nf_api._artifact_paths("测试用书1104"),
                                        ensure_ascii=False))
        ok = all(set(it) == {"key", "label", "path", "exists"} for it in payload)
        check("A7 每项恰好 {key,label,path,exists}", ok, payload)
        ok2 = all(isinstance(it["exists"], bool) and isinstance(it["path"], str)
                  for it in payload)
        check("A7 exists 是布尔、path 是字符串", ok2, payload)
    finally:
        nf_api.ROOT = saved_root

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
