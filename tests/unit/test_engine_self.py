# -*- coding: utf-8 -*-
"""engine: self —— 阶段只产任务书，由调用方执行（2026-11-04）。

## 要治的病

`engine: hermes / direct` 都要求**程序自己驱动模型**：每阶段起一次客户端 / 子会话。
外部 Agent 想接管时只有一条路 —— 看着它自己跑。而阶段完成判定本来就挂在盘上
产物（`stage_artifacts`），所以"程序只出任务书、执行方写产物、程序再按产物续跑"
是天然可行的。

## 判据（每条都能变红）

阶段侧（T）：
- T1 阶段 1~6 都有 `emit_tasks` + `self_done`（阶段 7/8 纯本地，故意没有）
- T2 每份任务书的协议头都含：目标产物路径 / 合格判据 / 自检命令
- T3 去掉协议头后**逐字等于**既有 `build_*_task` 的产物（不许出现 self 专用文本分叉）
- T4 全程零 LLM：把各阶段的 `make_client` 换成会抛异常的桩，`emit_tasks` 仍全过

编排侧（S）：
- S1 engine=self + 产物缺 → 出任务书 + rc 3 + progress 标 `awaiting_external`
- S2 任务书在、产物仍缺 → 重跑仍 rc 3，任务书**逐字节一致**（幂等）
- S3 产物补齐 → 该阶段直接 done，并继续为下一阶段出任务书
- S4 engine=direct 走原路径：不产任何 self 任务书（回归）
- S5 引擎判定的唯一来源认得 self；`make_client` 在 self 下必须**炸**（不许偷偷调模型）

用法：python tests/unit/test_engine_self.py
"""
import hashlib
import io
import json
import os
import sys
import shutil
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import self_tasks as st                    # noqa: E402
from utils.engine import resolve_engine               # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


SETTING = {
    "characters": [{"name": "阿禾", "identity": "药铺学徒", "personality": "谨慎话少",
                    "goal": "查清父亲的死因", "appearance": "青布短打"}],
    "world": {"locations": [{"name": "青石镇", "description": "山道尽头的旧镇。"}]},
    "plot_fragments": [{"summary": "阿禾发现药柜暗格", "status": "unused"}],
    "timeline": [{"when": "第一年春", "what": "父亲失踪"}],
}

CHAPTER = """# 第一章 暗格

{%s}

阿禾把药柜最后一格抽屉拉到底，木板发出干涩的响。里面空着，只有一层薄灰。

<!-- summary: 阿禾发现药柜暗格是空的，但灰上有指印。 -->
<!-- quality: 7 -->
""" % ("夜雨落在青石板上，" * 620)


def _w(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def make_ws(engine="self"):
    """最小可跑工作区（materials 有卡；设定/大纲/正文按需再补）。"""
    ws = Path(tempfile.mkdtemp(prefix="nf_self_"))
    _w(ws / "config" / "system.yaml",
       "engine: %s\nchapter:\n  target_words: [5000, 9000]\n  quality_threshold: 6\n"
       "gates:\n  require_approval: [2]\n  auto_retry: false\n  quality_gate: false\n"
       % engine)
    _w(ws / "config" / "project.yaml",
       "book:\n  name: 自任务夹具\n  genre: 悬疑\n  chapters: 2\n  target_words: 20000\n"
       "materials:\n  dir: materials/raw\n  use_scraps: false\n")
    _w(ws / "materials" / "raw" / "01.md", "主角是药铺学徒阿禾，父亲三年前失踪。\n")
    # 提示词模板按 **CWD 相对路径** 读（prompts/stage*.md）—— 夹具必须自带一份，
    # 否则每个阶段都会以"模板缺失"失败（那是工作区问题，不是引擎问题）。
    shutil.copytree(ROOT / "prompts", ws / "prompts")
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    return ws


class BoomClient:
    """任何模型调用都炸 —— 用来证明 emit_tasks 全程零 LLM。"""

    def write_task(self, *a, **k):
        raise AssertionError("engine=self 下不许构造任务给模型")

    def run_task(self, *a, **k):
        raise AssertionError("engine=self 下不许调用 run_task")

    def __getattr__(self, name):
        raise AssertionError("engine=self 下不许碰客户端属性: " + name)


def _cfg(ws):
    from utils.config_io import load_pipeline_config
    cwd = os.getcwd()
    os.chdir(ws)
    try:
        return load_pipeline_config()
    finally:
        os.chdir(cwd)


# ---------------------------------------------------------------- T 组
def t_group(ws):
    print("\nT 组：各阶段的任务书产出（零 token）")
    import stage1_consolidate as s1
    import stage2_outline as s2
    import stage3_chapter_outline as s3
    import stage4_writing as s4
    import stage5_check as s5
    import stage6_polish as s6

    stages = {1: s1, 2: s2, 3: s3, 4: s4, 5: s5, 6: s6}
    check("T1 阶段 1~6 都有 emit_tasks + self_done + settle（收尾可选）",
          all(hasattr(m, "emit_tasks") and hasattr(m, "self_done")
              for m in stages.values()),
          [n for n, m in stages.items() if not hasattr(m, "emit_tasks")])

    cfg, proj = _cfg(ws)
    # T4：把各阶段的 make_client 换成炸弹 —— 任何一步真去构造客户端都会立刻炸
    saved = {n: m.make_client for n, m in stages.items()}
    for m in stages.values():
        m.make_client = lambda *a, **k: BoomClient()

    cwd = os.getcwd()
    os.chdir(ws)
    try:
        # 补最小上游产物，让每个阶段都有活可干
        _w("data/setting/setting.json", json.dumps(SETTING, ensure_ascii=False, indent=2))
        _w("data/outline/global.md",
           "## 起\n雨夜。\n\n## 承\n暗格。\n\n## 转\n对峙。\n\n## 合\n收束。\n\n"
           "## 关键节点\n- 暗格被发现\n\n## 预计章节数\n3\n")
        for n in (1,):
            _w("data/outline/chapters/%02d.md" % n,
               "## 核心事件\n阿禾发现药柜暗格。\n\n## 涉及角色\n阿禾\n\n## 功能\n建立悬念\n\n"
               "## 小目标\n找到暗格里的东西\n\n## 悬念钩子\n灰上的指印是谁的\n")
        # 章 1：正文合格 + 已有 checked（→ 阶段 6 有活）
        # 章 2：正文合格、**没有** checked（→ 阶段 5 有活）
        # 章 3：还没写（→ 阶段 4 有活）
        _w("data/chapters/raw/01.md", CHAPTER)
        _w("data/chapters/raw/02.md", CHAPTER.replace("暗格", "药柜"))
        _w("data/chapters/checked/01.md", CHAPTER)

        results = {}
        for n, m in stages.items():
            try:
                results[n] = m.emit_tasks(cfg, proj, None, task_dir="data/state/tasks/self")
            except Exception as e:                              # noqa: BLE001
                results[n] = {"error": "%s: %s" % (type(e).__name__, e)}
                print("    stage%d emit 失败: %s" % (n, results[n]["error"]))

        bad = {n: r for n, r in results.items() if r.get("error")}
        check("T4 emit_tasks 全程零 LLM（客户端被换成炸弹仍全过）", not bad, bad)
        check("T2a 每个阶段都产出了 ≥1 份任务书",
              all(results[n].get("tasks") for n in (2, 3, 4, 5, 6)),
              {n: len(results[n].get("tasks") or []) for n in results})
        heads = []
        for n, r in results.items():
            for t in r.get("tasks") or []:
                text = Path(t["path"]).read_text(encoding="utf-8")
                tg = t["target"]
                tg = tg if isinstance(tg, list) else [tg]
                ok = ("必须出现的产物" in text and "合格判据" in text
                      and "自检命令" in text and all(x in text for x in tg))
                heads.append((n, Path(t["path"]).name, ok))
        check("T2b 每份任务书都写清了产物路径 / 合格判据 / 自检命令",
              heads and all(h[2] for h in heads),
              [h for h in heads if not h[2]][:3])

        # T5：产物路径必须是**候选区**（2026-11-12 产物闸：阶段 1~6 全部登记）。
        # 漏一个阶段 = 那个阶段的执行方又能直接写正式路径，锁就是假的。
        bad_t = []
        for n, r in results.items():
            for t in r.get("tasks") or []:
                tg = t.get("target")
                tg = tg if isinstance(tg, list) else [tg]
                for x in tg:
                    if not str(x).startswith("data/_pending/"):
                        bad_t.append((n, x))
        check("T5 阶段 1~6 的产物路径都在候选区（不许直写正式路径）", not bad_t, bad_t[:5])

        # T3：去掉协议头后与既有 build_*_task 逐字一致
        #   —— 每个阶段都用**它自己的**判据/分批函数重算，避免"测试里另写一套切法"。
        pairs = [(r["path"], s2.build_task(cfg, proj)) for r in (results[2].get("tasks") or [])]
        # 阶段 1：设定集归并任务书（正文必须就是 build_setting_task 的产物）
        from stage1_consolidate import SCRAPS_MERGE
        for r in (results[1].get("tasks") or []):
            if Path(r["path"]).name == "setting.md":
                pairs.append((r["path"], s1.build_setting_task(
                    proj, Path("data/setting/materials_manifest.json"),
                    "data/setting/normalized",
                    extra_inputs=[SCRAPS_MERGE] if Path(SCRAPS_MERGE).exists() else [])))

        _total3, _limit3, pending3 = s3._self_window(cfg, proj)
        if pending3:
            for bi, chs in enumerate(s3._self_groups(cfg, proj, pending3), 1):
                from utils.material_state import canon_or_setting
                pairs.append(("data/state/tasks/self/stage3/batch%02d.md" % bi,
                              s3.build_batch_task(cfg, proj, chs, "data/outline/global.md",
                                                  canon_or_setting())))

        _total4, pending4 = s4._self_pending(cfg, proj)
        if pending4:
            n0 = pending4[0]
            prev = Path("data/chapters/raw/%02d.md" % (n0 - 1))
            pairs.append(("data/state/tasks/self/stage4/ch%02d.md" % n0,
                          s4.build_chapter_task(
                              cfg, proj, n0, "data/outline/chapters/%02d.md" % n0,
                              "data/setting/setting.json", "data/summaries/rolling.md",
                              s4.extract_prev_tail(prev, 4) if prev.exists() else [])))

        _pending5, _batches5 = s5._self_batches(cfg, proj)
        for bi, batch in enumerate(_batches5, 1):
            bf = "\n".join("- %s" % f.resolve() for f in batch)
            pairs.append(("data/state/tasks/self/stage5/batch%02d.md" % bi,
                          s5.build_check_task(proj, Path("data/chapters/raw"),
                                              "data/setting/setting.json",
                                              Path("data/chapters/checked"),
                                              bf, bi, len(_batches5))))

        for f in s6._self_pending(proj):
            n0 = int(f.stem)
            pairs.append(("data/state/tasks/self/stage6/ch%02d.md" % n0,
                          s6.build_polish_task(proj, Path("data/chapters/checked"),
                                               Path("data/chapters/refined"), [n0],
                                               vol_index=None,
                                               report_path="data/outline/polish_ch%02d.md" % n0)))

        same = []
        for path, body in pairs:
            p = Path(path)
            if not p.exists():
                same.append((path, False))
                continue
            got = st.strip_protocol(p.read_text(encoding="utf-8"))
            # 2026-11-12：任务书现在会内联输入（材料靠注入）。期望值走**同一份实现**，
            # 这样"没有 self 专用分叉文本"这条判据仍然成立。
            from utils.llm_client import inline_inputs
            want = inline_inputs(body, note=st.INLINE_NOTE)[0]
            same.append((Path(path).name, got == want))
        check("T3 去掉协议头后与 build_*_task（经同一份内联）逐字相同",
              same and all(s[1] for s in same),
              [s for s in same if not s[1]][:3])

        # T6：任务书必须**自包含**（材料靠注入，不靠路径）。
        # 病：以前只写"输入文件：materials/raw/…（用 read_file 读取）"，
        # 执行方拿到任务书先 grep 整个仓库找线索 —— 找到别的书、别的稿，或者按文件名猜。
        print("\nT6：任务书自包含（材料靠注入，不靠路径）")
        still_paths, has_marker, has_content = [], 0, 0
        for n, r in results.items():
            for t in r.get("tasks") or []:
                text = Path(t["path"]).read_text(encoding="utf-8")
                if "（用 read_file 读取）" in text:
                    still_paths.append((n, Path(t["path"]).name))
                if "## 内联输入" in text:
                    has_marker += 1
                if "===== 文件:" in text:
                    has_content += 1
        check("T6 不再有“自己去 read_file 读”的段落", not still_paths, still_paths[:4])
        check("T6 任务书里真有【内联输入】区", has_marker > 0, has_marker)
        check("T6 内联区带真实文件内容（有 ===== 文件: 标记）", has_content > 0, has_content)
    finally:
        for n, fn in saved.items():
            stages[n].make_client = fn
        os.chdir(cwd)


def s_group(ws):
    print("\nS 组：orchestrator 的 self 分支")
    import orchestrator
    from utils.progress_manager import ProgressManager

    cwd = os.getcwd()
    os.chdir(ws)
    try:
        rc = orchestrator.run(from_stage=1)
        pg = ProgressManager("data/state/progress.json")
        s1 = pg.data["stages"]["1"]
        tasks = s1.get("tasks") or []
        check("S1 产物缺 → rc=3 且阶段标 awaiting_external",
              rc == 3 and s1.get("status") == "awaiting_external", (rc, s1.get("status")))
        check("S1 任务书真的落盘且被登记进 progress",
              len(tasks) == 1 and Path(tasks[0]).exists(), tasks)

        before = _sha(tasks[0]) if tasks else ""
        rc2 = orchestrator.run(from_stage=1)
        pg = ProgressManager("data/state/progress.json")
        s1 = pg.data["stages"]["1"]
        tasks2 = s1.get("tasks") or []
        check("S2 重跑仍 rc=3（产物还没写），任务书路径不变",
              rc2 == 3 and tasks2 == tasks, (rc2, tasks2))
        check("S2 任务书幂等（逐字节一致）",
              bool(before) and _sha(tasks2[0]) == before)

        # 产物补齐 → 阶段 1 done，接着为阶段 2 出任务书
        # 2026-11-12：产物闸下"补齐"= 执行方写候选 + 作者批准（直写正式路径已经不算数）
        from utils import product_gate as _pg
        _body = json.dumps(SETTING, ensure_ascii=False, indent=2)
        _w(_pg.pending_path(".", 1, "data/setting/setting.json"), _body)
        _pg.record_approval(".", 1, "data/setting/setting.json", by="fixture")
        rc3 = orchestrator.run(from_stage=1)
        pg = ProgressManager("data/state/progress.json")
        s1 = pg.data["stages"]["1"]
        s2 = pg.data["stages"]["2"]
        check("S3 产物补齐 → 阶段 1 直接 done（不重新出任务书）",
              s1.get("status") == "done", s1.get("status"))
        check("S3 接着为阶段 2 出任务书并停（rc=3）",
              rc3 == 3 and s2.get("status") == "awaiting_external",
              (rc3, s2.get("status")))
    finally:
        os.chdir(cwd)


def s4_group():
    print("\nS4/S5：其它引擎不受影响 + 判定来源")
    ws = make_ws(engine="direct")
    _w(ws / "data" / "setting" / "setting.json",
       json.dumps(SETTING, ensure_ascii=False, indent=2))
    # 清空素材：这样 stage1 走"无新素材 → 复用既有设定集"这条**零模型**路径，
    # 正好用来验"direct 下行为不变"（真要归并就会去调客户端，那属于另一条路径）。
    (ws / "materials" / "raw" / "01.md").unlink()
    import orchestrator
    from utils.progress_manager import ProgressManager

    cwd = os.getcwd()
    os.chdir(ws)
    try:
        # 无素材 + 有效设定集 → stage1 走"复用设定集"，rc 0，且不产 self 任务书。
        # 传 dummy client：direct 下 stage1 会 `client or make_client(...)`，
        # 这里既不想连网也不想它真去构造客户端。
        rc = orchestrator.run(from_stage=1, only_stage=1, client=BoomClient())
        pg = ProgressManager("data/state/progress.json")
        produced = list(Path("data/state/tasks/self").rglob("*.md"))
        check("S4 engine=direct 不产任何 self 任务书（原有路径不变）",
              not produced and rc == 0, (rc, [p.name for p in produced]))
        check("S4 engine=direct 的阶段 1 仍按原语义标 done",
              pg.data["stages"]["1"].get("status") == "done",
              pg.data["stages"]["1"].get("status"))
    finally:
        os.chdir(cwd)

    check("S5 引擎判定认得 self（不再打成未知值后回落 direct）",
          resolve_engine({"engine": "self"}) == "self"
          and resolve_engine({}) == "direct"
          and resolve_engine({"engine": "hermes"}) == "hermes")
    from utils.llm_client import make_client
    try:
        make_client({"engine": "self"}, "default", verbose=False)
        check("S5 engine=self 下 make_client 必须炸（不许偷偷调模型）", False)
    except RuntimeError as e:
        check("S5 engine=self 下 make_client 必须炸（不许偷偷调模型）",
              "self" in str(e), str(e)[:120])


def main():
    print("=" * 66)
    print("engine: self —— 阶段只产任务书")
    print("=" * 66)
    ws = make_ws()
    t_group(ws)
    s_group(make_ws())
    s4_group()
    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAIL:", FAIL)
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
