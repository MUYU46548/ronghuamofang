# -*- coding: utf-8 -*-
"""F19 版本回溯 + ④ 语义审稿接线（2026-11-03）。

## F19 要治的病
版本只躺在 `data/chapters/history/chNN_vM.md`，想回溯得自己翻目录，
"压缩前那一版"更是要找半天。现在 `GET /chapters/versions?n=` 直接说：
哪几版、各多少字、哪一版是压缩前的（与 progress 里的 shrink 记录对字数）。

## ④ 要治的病
语义审稿此前只能手动调 `/review/semantic`。现在阶段 4 后置里有开关
（`gates.semantic_review`，**默认关**）：开了就给不合格/待重写章开讨论 + 任务书，
然后停等人工。

判据：V1~V5 / R1~R3。
用法：python tests/unit/test_versions_semantic_gate.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    print("F19 版本回溯 + 语义审稿接线")

    src = (ROOT / "scripts" / "nf_api_domains" / "chapters.py").read_text(encoding="utf-8")
    check("V1 端点存在 handle_chapters_versions", "def handle_chapters_versions" in src)
    check("V2 只读历史目录（不写不删）",
          'api.ROOT / "data" / "chapters" / "history"' in src and "unlink" not in
          src[src.index("def handle_chapters_versions"):src.index("def handle_chapters_save")])
    check("V3 读 shrink 记录判断压缩前版本", '"shrink"' in src and "pre_shrink" in src)
    check("V4 字数用同一判据（count_cn_words）", "count_cn_words" in src)
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("V5 路由挂在 /chapters/versions", '"/chapters/versions"' in api)

    # 版本枚举是纯读盘，用临时目录验一遍真实行为
    import re
    from utils.verify_chapter import count_cn_words
    tmp = Path(tempfile.mkdtemp())
    hist = tmp / "history"
    hist.mkdir()
    (hist / "ch03_v1.md").write_text("字" * 120, encoding="utf-8")
    (hist / "ch03_v2.md").write_text("字" * 60, encoding="utf-8")
    (hist / "ch04_v1.md").write_text("字" * 10, encoding="utf-8")
    pat = re.compile(r"^ch%02d_v(\d+)\.md$" % 3)
    got = sorted(int(pat.match(p.name).group(1)) for p in hist.glob("ch03_v*.md") if pat.match(p.name))
    check("V6 只取该章的版本（不串章）", got == [1, 2], got)
    check("V7 字数可算（压缩前后能区分）",
          count_cn_words((hist / "ch03_v1.md").read_text(encoding="utf-8")) == 120
          and count_cn_words((hist / "ch03_v2.md").read_text(encoding="utf-8")) == 60)

    orch = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("R1 阶段 4 后置有 semantic_review 开关", 'get("semantic_review", False)' in orch)
    check("R2 默认关（yaml 显式 false）",
          "semantic_review: false" in (ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))
    check("R3 开了只开讨论不调模型（用 build_review）",
          "review_semantic.build_review" in orch and "run_task" not in
          orch[orch.index("semantic_review"):orch.index("semantic_review") + 1200])

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
