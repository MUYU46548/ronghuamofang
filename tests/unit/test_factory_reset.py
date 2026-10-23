# -*- coding: utf-8 -*-
"""出厂测试项目一键还原（utils/factory_reset）判据。

边界（用户 2026-10-23 定调）：**仅出厂测试项目**可一键还原 —— 真书没有
出厂标记，这条路天然碰不到。三重自检（标记 / 书名 / 出厂源）任一不过 =
一个字节都不动；先快照后动手；旧素材先备份。

覆盖：
 1. 标记三件套（写/读/清）
 2. describe：无标记 / 书名不符 / 源缺失 → is_factory=false + 原因
 3. reset：无标记拒动（零写入）
 4. reset 全流程：清书状态 + 铺回出厂素材 + 配置写回 + 验证 + 白名单保留
 5. reset 自检：书名不符拒动、源缺失拒动、源不完整拒动（都零写入）
 6. 快照失败即中止（无回退点不动手）
 7. 端点护栏：403 非 GUI / 400 无 confirm / 名单在 FORBIDDEN_IN_AGENT_MODE
"""
import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import factory_reset as fr                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:140]) if (detail and not cond) else ""))


def _build_preset_src(base, pid="unit-preset", cards=3, min_cards=1):
    """examples/presets/<pid>/：preset.yaml + N 张卡。返回预设目录。"""
    src = base / "examples" / "presets" / pid
    (src / "materials" / "raw").mkdir(parents=True, exist_ok=True)
    (src / "preset.yaml").write_text(
        "id: %s\nname: 单测预设书\ngenre: 悬疑\nchapters: 3\n"
        "verify:\n  min_cards: %d\n" % (pid, min_cards), encoding="utf-8")
    for i in range(1, cards + 1):
        (src / "materials" / "raw" / ("卡%d.md" % i)).write_text(
            "# 卡%d\n\n单测素材。\n" % i, encoding="utf-8")
    return src


def _build_workspace(base, book_name="单测预设书"):
    """带测试进度的出厂项目工作区。返回 root。"""
    raw = base / "materials" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (base / "config").mkdir(parents=True, exist_ok=True)
    (base / "config" / "project.yaml").write_text(
        "book:\n  name: %s\n  genre: 悬疑\n  chapters: 3\n" % book_name,
        encoding="utf-8")
    # 测试进度：章节 / 大纲 / 设定 / 评审 / progress / tasks
    for rel, content in (
            ("data/chapters/raw/01.md", "## 第1章 起\n\n测试进度。\n"),
            ("data/outline/global.md", "# 大纲\n"),
            ("data/setting/setting.json", "{}"),
            ("data/review/01.json", "{}"),
            ("data/state/tasks/stage4_x.md", "任务"),
    ):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    (base / "data/state").mkdir(parents=True, exist_ok=True)
    (base / "data/state/progress.json").write_text(
        json.dumps({"stages": {"1": {"status": "done"}}}), encoding="utf-8")
    # 脏素材（3 张，出厂源是另一套）
    for i in range(1, 4):
        (raw / ("旧卡%d.md" % i)).write_text("# 旧卡%d\n" % i, encoding="utf-8")
    # 白名单资产（还原绝不能动）
    for rel in ("data/books/雾港核心/_meta.json", "data/presets/我的预设.json",
                "history/20261023_000000_x/b.md", "output/书_完整版.docx",
                "logs/runs.db", "data/state/factory_manifest.json",
                "data/state/agent_audit.jsonl"):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("保留", encoding="utf-8")
    # 出厂源
    _build_preset_src(base)
    fr.write_marker(base, "preset", "unit-preset", book_name)
    return base


def _pin_modules(tmp):
    """把写盘模块的根常量钉到夹具（同 test_export_reports 的做法），
    并把 snapshot 换成记账假实现（断言「先快照」且不碰真仓库 history）。"""
    import utils.project_config as pc
    import snapshot as snap_mod
    state = {"snapshot_called": []}
    orig_py = pc.PROJECT_YAML
    orig_snap = snap_mod.snapshot
    pc.PROJECT_YAML = tmp / "config" / "project.yaml"
    snap_mod.snapshot = lambda tag: state["snapshot_called"].append(tag)

    def restore():
        pc.PROJECT_YAML = orig_py
        snap_mod.snapshot = orig_snap
    return restore, state


def case_marker_roundtrip(tmp):
    print("\n[1] 标记三件套")
    p = fr.marker_path(tmp)
    check("初始无标记", fr.read_marker(tmp) is None and not p.exists())
    fr.write_marker(tmp, "preset", "rail-end", "铁轨尽头")
    m = fr.read_marker(tmp)
    check("写后可读且字段齐",
          m and m["kind"] == "preset" and m["id"] == "rail-end"
          and m["book_name"] == "铁轨尽头" and "applied_at" in m, m)
    check("清除后消失", fr.clear_marker(tmp) and fr.read_marker(tmp) is None)
    check("再清幂等不炸", fr.clear_marker(tmp) is False)


def case_describe_refusals(tmp):
    print("\n[2] describe：三种拒因")
    # 2a. 无标记
    ws = _build_workspace(tmp / "ws_a")
    fr.clear_marker(ws)
    d = fr.describe(ws)
    check("无标记 → is_factory=false", d["is_factory"] is False, d)
    check("无标记原因点名出厂标记", "出厂标记" in (d["reason"] or ""), d["reason"])
    # 2b. 书名不符（用户换过书）
    ws = _build_workspace(tmp / "ws_b")
    (ws / "config" / "project.yaml").write_text(
        "book:\n  name: 别的书\n", encoding="utf-8")
    d = fr.describe(ws)
    check("书名不符 → is_factory=false", d["is_factory"] is False, d)
    check("书名不符原因含两方书名",
          "别的书" in (d["reason"] or "") and "单测预设书" in (d["reason"] or ""),
          d["reason"])
    # 2c. 出厂源缺失
    ws = _build_workspace(tmp / "ws_c")
    shutil.rmtree(ws / "examples")
    d = fr.describe(ws)
    check("源缺失 → is_factory=false", d["is_factory"] is False, d)
    # 2d. 源不完整（卡数 < min_cards）
    ws = _build_workspace(tmp / "ws_d")
    (ws / "examples/presets/unit-preset/preset.yaml").write_text(
        "id: unit-preset\nname: 单测预设书\nverify:\n  min_cards: 9\n",
        encoding="utf-8")
    d = fr.describe(ws)
    check("源不完整 → is_factory=false", d["is_factory"] is False, d)
    # 2e. 正例：一切就绪
    ws = _build_workspace(tmp / "ws_e")
    d = fr.describe(ws)
    check("就绪 → is_factory=true + source_ok",
          d["is_factory"] is True and d["source_ok"] is True, d)
    check("wipe_plan 章数/卡数对",
          d["wipe_plan"]["chapters_done"] == 1
          and d["wipe_plan"]["cards_now"] == 3
          and d["wipe_plan"]["cards_factory"] == 3, d["wipe_plan"])


def case_reset_refusals(tmp):
    print("\n[3] reset：自检不过 = 零写入")
    for label, mutate in (
            ("无标记", lambda ws: fr.clear_marker(ws)),
            ("书名不符", lambda ws: (ws / "config" / "project.yaml").write_text(
                "book:\n  name: 别的书\n", encoding="utf-8")),
            ("源缺失", lambda ws: shutil.rmtree(ws / "examples")),
    ):
        ws = _build_workspace(tmp / ("ws_refuse_" + label))
        mutate(ws)
        before = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
        ok, rep = fr.reset(ws)
        after = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
        check("%s → 拒绝且报原因" % label, ok is False and rep.get("error"), rep.get("error"))
        check("%s → 文件零变化" % label, before == after)


def case_reset_happy(tmp):
    print("\n[4] reset 全流程：清干净、铺回来、白名单保留、验证在")
    ws = _build_workspace(tmp / "ws_happy")
    restore, snap_state = _pin_modules(ws)
    try:
        ok, rep = fr.reset(ws)
        check("返回 ok", ok is True, rep.get("error"))
        check("先快照（记账假实现被调用）", snap_state["snapshot_called"] == ["factory_reset"])
        # 书状态已清
        gone = [rel for rel in ("data/chapters", "data/outline", "data/setting",
                                "data/review", "data/state/tasks",
                                "data/state/progress.json")
                if (ws / rel).exists()]
        check("书状态已清（章节/大纲/设定/评审/tasks/progress）", not gone, gone)
        # 素材铺回出厂
        cards = sorted(p.name for p in (ws / "materials" / "raw").glob("*.md"))
        check("素材回到出厂 3 张且是源里的名字",
              cards == ["卡1.md", "卡2.md", "卡3.md"], cards)
        # 配置写回（set_book_fields 落到钉住的 project.yaml）
        import utils.project_config as pc
        cur = (pc.load_project_yaml(ws / "config" / "project.yaml") or {}).get("book") or {}
        check("配置写回书名/题材/章数",
              cur.get("name") == "单测预设书" and cur.get("genre") == "悬疑"
              and cur.get("chapters") == 3, cur)
        # 白名单保留
        keep = [rel for rel in ("data/books/雾港核心/_meta.json",
                                "data/presets/我的预设.json",
                                "history/20261023_000000_x/b.md",
                                "output/书_完整版.docx", "logs/runs.db",
                                "data/state/factory_manifest.json",
                                "data/state/agent_audit.jsonl")
                if not (ws / rel).exists()]
        check("白名单资产全部保留", not keep, keep)
        # 旧素材有备份
        bak = list((ws / "data/state/preset_backup").rglob("旧卡1.md"))
        check("旧素材已备份到 preset_backup", bool(bak))
        # 验证字段 + 标记仍在（仍是一本可再还原的出厂项目）
        check("报告带 verified 且通过",
              rep.get("verified", {}).get("cards_after") == 3
              and rep["verified"].get("progress_cleared") is True, rep.get("verified"))
        check("标记仍在（可再次还原）", fr.read_marker(ws) is not None)
        # 还原后 describe 仍绿（幂等：再跑一次也应成功）
        ok2, rep2 = fr.reset(ws)
        check("幂等：再还原一次照样成功", ok2 is True, rep2.get("error"))
    finally:
        restore()


def case_snapshot_gate(tmp):
    print("\n[5] 快照失败即中止（无回退点不动手）")
    ws = _build_workspace(tmp / "ws_snapfail")
    import snapshot as snap_mod
    orig = snap_mod.snapshot
    snap_mod.snapshot = lambda tag: (_ for _ in ()).throw(RuntimeError("磁盘满"))
    try:
        before = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
        ok, rep = fr.reset(ws)
        after = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
        check("快照失败 → 拒绝且点名快照", ok is False and "快照失败" in (rep.get("error") or ""),
              rep.get("error"))
        check("文件零变化", before == after)
    finally:
        snap_mod.snapshot = orig


def case_endpoint_guards(tmp):
    print("\n[6] 端点护栏：403 非 GUI / 400 无 confirm / 名单在场")
    import nf_api as api_mod
    import nf_api_domains.factory as dom
    from utils import agent_guard

    TOKEN = "unit-test-gui-token"
    ws = _build_workspace(tmp / "ws_api")
    orig_py = None
    import utils.project_config as pc
    orig_py = pc.PROJECT_YAML
    pc.PROJECT_YAML = ws / "config" / "project.yaml"
    api_mod._set_root(ws)

    class FakeH:
        """双因子经 headers + 环境 token（与 _gui_only → agent_guard 同一条路）。"""
        def __init__(self, trusted):
            self.headers = {}
            if trusted:
                self.headers = {"X-Mofang-Source": "gui", "X-Mofang-Token": TOKEN}
    os.environ["NF_GUI_TOKEN"] = TOKEN
    try:
        # 只读盘点：GET 可用（agent 允许）
        st, payload = dom.handle_factory_project(FakeH(False))
        check("GET 只读可用且反映 is_factory", st == 200 and payload.get("is_factory") is True,
              (st, payload))
        # 403：非 GUI 来源（即便 confirm:true 也拒）
        st403, p403 = dom.handle_factory_project_reset(FakeH(False), {"confirm": True})
        check("非 GUI → 403", st403 == 403 and p403.get("ok") is False, (st403, p403))
        check("403 时文件零变化（没 confirm 前就拦了）",
              (ws / "data/chapters/raw/01.md").exists())
        # 400：GUI 但缺 confirm
        st400, p400 = dom.handle_factory_project_reset(FakeH(True), {})
        check("GUI 缺 confirm → 400", st400 == 400 and p400.get("ok") is False, (st400, p400))
        # 名单在场（agent 模式的第二道闸）
        check("在 FORBIDDEN_IN_AGENT_MODE 名单",
              "/factory/project/reset" in agent_guard.FORBIDDEN_IN_AGENT_MODE)
        # 真跑（GUI + confirm）：临时把 snapshot 换假的防碰真仓库
        import snapshot as snap_mod
        orig_snap = snap_mod.snapshot
        snap_mod.snapshot = lambda tag: None
        try:
            st_ok, p_ok = dom.handle_factory_project_reset(FakeH(True), {"confirm": True})
            check("GUI + confirm → 200 且还原成功", st_ok == 200 and p_ok.get("ok") is True,
                  (st_ok, str(p_ok)[:160]))
        finally:
            snap_mod.snapshot = orig_snap
    finally:
        pc.PROJECT_YAML = orig_py
        api_mod._set_root(ROOT)
        os.environ.pop("NF_GUI_TOKEN", None)


def case_hooks_present():
    print("\n[7] 钩子在场（铺/换书与标记成对）")
    import preset_apply as pa
    import switch_book as sb
    check("preset_apply.apply 成功路径写标记", "write_marker" in inspect.getsource(pa.apply))
    check("switch_book.archive 清标记", "clear_marker" in inspect.getsource(sb.archive))
    check("switch_book.init_empty 清标记", "clear_marker" in inspect.getsource(sb.init_empty))


def case_implicit_sample(tmp):
    print("\n[8] 隐式样例：书名 == 随包样例名（无标记也算出厂测试项目）")
    # 用户实况（2026-10-23 纠正）：《雾港核心》就是随包样例书直接跑完的
    # 测试项目，没有铺设标记 —— 书名与样例一致即可认出。
    sample = tmp / "examples" / "sample-book"
    (sample / "materials").mkdir(parents=True)   # 样例布局：卡在 materials/ 直下
    (sample / "project.yaml").write_text(
        "book:\n  name: 雾港核心\n  genre: 悬疑\n  chapters: 2\n", encoding="utf-8")
    for i in range(1, 4):
        (sample / "materials" / ("样例卡%d.md" % i)).write_text(
            "# 样例卡%d\n" % i, encoding="utf-8")
    # 样例源钉到夹具（同打包态主进程注入 NF_SAMPLE_ROOT 的路，防回落真仓库样例）
    os.environ["NF_SAMPLE_ROOT"] = str(sample)
    ws = tmp / "ws_implicit"
    raw = ws / "materials" / "raw"
    raw.mkdir(parents=True)
    (ws / "config").mkdir(parents=True)
    (ws / "config" / "project.yaml").write_text(
        "book:\n  name: 雾港核心\n  genre: 悬疑\n  chapters: 2\n", encoding="utf-8")
    for i in range(1, 6):
        (raw / ("跑过的卡%d.md" % i)).write_text("# 跑过的卡%d\n" % i, encoding="utf-8")
    for rel, content in (("data/chapters/raw/01.md", "## 第1章\n\n跑完的进度。\n"),
                         ("data/outline/global.md", "# 大纲\n"),
                         ("data/state/progress.json",
                          json.dumps({"stages": {"4": {"status": "done"}}}))):
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    d = fr.describe(ws)
    check("无标记但书名==样例名 → is_factory=true", d["is_factory"] is True, d)
    check("身份= sample（隐式样例）", d.get("kind") == "sample", d.get("kind"))
    check("wipe_plan 章数/卡数对",
          d["wipe_plan"]["chapters_done"] == 1 and d["wipe_plan"]["cards_now"] == 5
          and d["wipe_plan"]["cards_factory"] == 3, d["wipe_plan"])
    restore, snap_state = _pin_modules(ws)
    try:
        ok, rep = fr.reset(ws)
        check("还原成功", ok is True, rep.get("error"))
        cards = sorted(p.name for p in raw.glob("*.md"))
        check("素材回到样例 3 张", cards == ["样例卡1.md", "样例卡2.md", "样例卡3.md"], cards)
        check("跑完的进度清掉",
              not (ws / "data/chapters/raw/01.md").exists()
              and not (ws / "data/state/progress.json").exists())
        check("旧素材有备份", bool(list((ws / "data/state/preset_backup").rglob("跑过的卡1.md"))))
    finally:
        restore()
    # 书名不同 → 不认
    ws2 = tmp / "ws_implicit_other"
    (ws2 / "config").mkdir(parents=True)
    (ws2 / "config" / "project.yaml").write_text("book:\n  name: 别的书\n", encoding="utf-8")
    check("书名不同 → 不认", fr.describe(ws2)["is_factory"] is False)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="factreset_"))
    try:
        case_marker_roundtrip(tmp)
        case_describe_refusals(tmp)
        case_reset_refusals(tmp)
        case_reset_happy(tmp)
        case_snapshot_gate(tmp)
        case_endpoint_guards(tmp)
        case_hooks_present()
        case_implicit_sample(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("NF_SAMPLE_ROOT", None)
    print("\n==== 结果: PASS %d / FAIL %d ====" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
