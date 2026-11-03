# -*- coding: utf-8 -*-
"""新手序列引导（I-1，2026-11-03）。

## 要治的病

作者原话：\"随便找个破游戏的新手教程都更好\"。旧界面是五条并列文本，
不告诉你\"现在在哪一步、下一步点哪个按钮\"。

## 判据（每条都能变红）

- O1 空工作区 → 下一步 = 建项目
- O2 有书没素材 → 下一步 = 放素材（序列严格，只有一个 current）
- O3 素材/模型齐了 → 下一步 = 阶段 1
- O4 阶段 1/2 已 done 但没审批 → 审批门抢到 current（不放行不该继续烧 token）
- O5 全部放行 + 有正文 → 下一步 = 导出
- O6 有 Word 成品 → 全 done，next 为 None
- O7 占位书名（示例书名（待填写））不算\"建好了\"
- O8 任意状态下 current 最多一个，且 done 数 = 统计值
- O9 snapshot 只读盘上事实（素材卡计数 / output docx / progress stages）
- O10 端点已接线（GET /onboarding/status 有转发）

用法：python tests/unit/test_onboarding.py
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


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _snap(**kw):
    base = {
        "book_name": "示例书名（待填写）",
        "book_chapters": 3,
        "materials_cards": 0,
        "materials_scraps": 0,
        "model_ready": False,
        "model_why": "未选择默认模型",
        "setting_exists": False,
        "outline_exists": False,
        "chapters_raw": 0,
        "word_exists": False,
        "stages": {},
        "gates": [],
        "has_progress": False,
    }
    base.update(kw)
    return base


def main():
    print("=" * 66)
    print("新手序列引导")
    print("=" * 66)

    from nf_api_domains import onboarding as ob

    print("\n[O1] 空工作区 → 建项目")
    r = ob.compute_steps(_snap())
    check("next = project", (r["next"] or {}).get("id") == "project", r["next"])
    check("current 只有 1 个", sum(1 for s in r["steps"] if s["state"] == "current") == 1)
    check("进度 0/8", r["progress"] == {"done": 0, "total": 8}, r["progress"])

    print("\n[O2] 有书没素材 → 放素材")
    r = ob.compute_steps(_snap(book_name="潮汐档案"))
    check("next = materials", (r["next"] or {}).get("id") == "materials", r["next"])
    check("project 标 done", r["steps"][0]["state"] == "done")
    check("后续步骤全是 todo",
          all(s["state"] == "todo" for s in r["steps"][2:]), [s["state"] for s in r["steps"]])

    print("\n[O3] 素材 + 模型就绪 → 阶段 1")
    r = ob.compute_steps(_snap(book_name="潮汐档案", materials_cards=4, model_ready=True))
    check("next = stage1", (r["next"] or {}).get("id") == "stage1", r["next"])
    check("evidence 带卡数", "4" in r["steps"][1]["evidence"], r["steps"][1]["evidence"])

    print("\n[O4] 阶段 1/2 done 未审批 → 审批门抢 current")
    snap = _snap(book_name="潮汐档案", materials_cards=4, model_ready=True,
                 setting_exists=True, outline_exists=True,
                 stages={1: {"status": "done"}, 2: {"status": "done"}},
                 gates=[{"stage": 1}, {"stage": 2}])
    r = ob.compute_steps(snap)
    check("next = gate", (r["next"] or {}).get("id") == "gate", r["next"])
    check("gate 的 current 只有一个",
          sum(1 for s in r["steps"] if s["state"] == "current") == 1)
    check("gate evidence 说明几处待审", "2 处" in r["steps"][5]["evidence"],
          r["steps"][5]["evidence"])
    check("stage4 仍 todo", r["steps"][6]["state"] == "todo", r["steps"][6]["state"])

    print("\n[O5] 已放行 + 有正文 → 导出")
    stages = {n: {"status": "done", "approved": True} for n in (1, 2, 3, 4, 5, 6)}
    r = ob.compute_steps(_snap(book_name="潮汐档案", materials_cards=4, model_ready=True,
                               setting_exists=True, outline_exists=True, chapters_raw=3,
                               stages=stages, gates=[]))
    check("next = export", (r["next"] or {}).get("id") == "export", r["next"])
    check("stage4 done", r["steps"][6]["state"] == "done", r["steps"][6])

    print("\n[O6] 有 Word 成品 → 全 done")
    r = ob.compute_steps(_snap(book_name="潮汐档案", materials_cards=4, model_ready=True,
                               setting_exists=True, outline_exists=True, chapters_raw=3,
                               word_exists=True, stages=stages, gates=[]))
    check("next = None", r["next"] is None, r["next"])
    check("done = 8", r["progress"]["done"] == 8, r["progress"])

    print("\n[O7] 占位书名不算建好")
    r = ob.compute_steps(_snap(book_name="示例书名（待填写）"))
    check("project 不是 done", r["steps"][0]["state"] != "done", r["steps"][0])
    check("next = project", (r["next"] or {}).get("id") == "project")
    r2 = ob.compute_steps(_snap(book_name="   "))
    check("空书名也不算", r2["steps"][0]["state"] != "done")

    print("\n[O8] 每个 step 都有 how 与 action（界面不用自己编）")
    r = ob.compute_steps(_snap())
    check("全部带 how", all(s.get("how") for s in r["steps"]))
    check("全部带 action", all(s.get("action") for s in r["steps"]))
    check("action.kind 合法",
          all(s["action"].get("kind") in ("tab", "new_project") for s in r["steps"]),
          [s["action"] for s in r["steps"]])

    print("\n[O9] snapshot 只读盘上事实")
    import nf_api as api
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir(parents=True)
        (root / "config" / "project.yaml").write_text(
            "book:\n  name: \"测试书\"\n  chapters: 2\n", encoding="utf-8")
        (root / "config" / "system.yaml").write_text(
            "engine: hermes\nmodel:\n  default:\n    provider: tokenhub\n    id: x\n",
            encoding="utf-8")
        (root / "materials" / "raw").mkdir(parents=True)
        (root / "materials" / "raw" / "a.md").write_text("x", encoding="utf-8")
        (root / "materials" / "raw" / "b.json").write_text("{}", encoding="utf-8")
        (root / "materials" / "raw" / "_backup").mkdir()
        (root / "materials" / "raw" / "_backup" / "old.md").write_text("x", encoding="utf-8")
        (root / "data" / "state").mkdir(parents=True)
        (root / "data" / "state" / "progress.json").write_text(json.dumps(
            {"stages": {"1": {"status": "done"}}}), encoding="utf-8")
        (root / "output").mkdir()
        (root / "output" / "书.docx").write_bytes(b"PK")

        old = api.ROOT
        api.ROOT = root
        try:
            snap = ob.snapshot()
        finally:
            api.ROOT = old
        check("书名读到", snap["book_name"] == "测试书", snap["book_name"])
        check("素材卡 2 张（跳过 _backup）", snap["materials_cards"] == 2,
              snap["materials_cards"])
        check("hermes 引擎算模型就绪", snap["model_ready"] is True, snap["model_why"])
        check("stage1 done", (snap["stages"].get(1) or {}).get("status") == "done")
        check("Word 成品可见", snap["word_exists"] is True)

    print("\n[O10] 端点接线")
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("GET /onboarding/status 有转发", 'elif p == "/onboarding/status":' in src)
    check("域模块被 import", "onboarding as dom_onboarding" in src)
    check("handler 返回 2 元组", ob.handle_onboarding_status(None)[0] in (200, 500))

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
