# -*- coding: utf-8 -*-
"""重写单章（2026-10-26，用户：「如何整章重写？当前已有跑完的测试项目，我想随便找一章跑」）。

## 为什么需要它

用户想验证"加强模式能不能把弱模型凑够 5000 字"，但 GUI 里没有"重写整章"入口：
- 「编辑 → 段落批注」是**精修一段**，只在旧稿上改，不触发加强模式
- 命令行删文件再跑 orchestrator 对用户不可接受

而这里有一个必须做对的坑：`stage4` 判断"这章要不要写"看的是**盘上文件的合格性**
（`is_chapter_complete`），不是 progress 标记。所以：
- 只清 `completed_chapters` → 文件还在且合格 → 判已完成，不写
- 文件合格但标记缺失 → 当"孤儿章"**只补账、不重调 LLM**（更隐蔽）

**必须让文件在盘上不存在**，而且要留备份（stage4 本身不备份）。

## 判据

A. `_prepare_chapter_rewrite`：旧稿移到 rewrite_backup/、原路径消失、完成标记清除
B. 备份是**移动**不是复制（原位置不能再留一份，否则 stage4 又判它完成）
C. `NF_STAGE4_ONLY` 让 stage4 只写指定章（别的章一次都不调）
D. 端点参数校验：非法章号 400
E. 前端有「重写」按钮且调的是 /chapters/rewrite

全程离线、零 LLM。
用法：python tests/unit/test_chapter_rewrite.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("NF_ROOT", None)

from utils.fake_client import FakeClient                # noqa: E402
from utils.progress_manager import ProgressManager      # noqa: E402
import stage4_writing as s4                             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


SETTING = {
    "characters": [{"id": "luxi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [],
    "_meta": {"version": 1, "generated_from": ["fake"]},
}


class CountingClient(FakeClient):
    """数写作调用（流式与整章两种都数 —— stage4 现在走流式）。"""

    def __init__(self):
        self.main_calls = []

    def _count(self, task_file):
        name = Path(task_file).name
        if name.startswith("stage4_ch") and "_expand" not in name and "_g" not in name:
            self.main_calls.append(name)

    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        self._count(task_file)
        return super().run_task(task_file, workdir=workdir, model=model)

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None,
                        model=None, session_id=None):
        self._count(task_file)
        return super().run_task_stream(task_file, on_piece, stop_flag,
                                       workdir=workdir, model=model)


def build_workspace(total=3, tag="rw"):
    tmp = Path(tempfile.mkdtemp(prefix="nf_rw_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "重写测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    sys_yaml = (ROOT / "config" / "system.yaml").read_text(encoding="utf-8")
    (tmp / "config" / "system.yaml").write_text(sys_yaml, encoding="utf-8")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 重写测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n- 功能：承接\n" % n,
            encoding="utf-8")
    return tmp


def run_stage4(tmp, client):
    import yaml
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, None, client=client,
                           task_dir=str(tmp / "data" / "state" / "tasks"))
    return ok, msg, progress


def main():
    import nf_api

    origin = os.getcwd()
    tmp = build_workspace(total=3, tag="a")
    try:
        os.chdir(tmp)
        nf_api._set_root(tmp)

        # 先把 3 章都写出来（模拟"已跑完的测试项目"）
        c0 = CountingClient()
        run_stage4(tmp, c0)
        raw = tmp / "data" / "chapters" / "raw"
        check("前置：3 章都已写完", len(list(raw.glob("*.md"))) == 3,
              sorted(p.name for p in raw.glob("*.md")))

        print("\n【A】重写前的准备：移走旧稿 + 备份 + 清标记 + 作废旧段群")
        before = (raw / "02.md").read_text(encoding="utf-8")
        parts = tmp / "data" / "chapters" / "_parts" / "02"
        parts.mkdir(parents=True, exist_ok=True)
        (parts / "g1.md").write_text("旧群正文" * 200, encoding="utf-8")
        bak_rel = nf_api._prepare_chapter_rewrite(2)
        check("返回备份相对路径", bool(bak_rel), bak_rel)
        check("备份文件真的存在",
              bool(bak_rel) and (tmp / bak_rel).exists(), bak_rel)
        check("备份内容 = 改前原文",
              bool(bak_rel) and (tmp / bak_rel).read_text(encoding="utf-8") == before)
        check("备份落在 rewrite_backup/",
              bool(bak_rel) and "rewrite_backup" in bak_rel, bak_rel)
        check("原位置**不再有**该章（否则 stage4 又判它已完成）",
              not (raw / "02.md").exists(),
              sorted(p.name for p in raw.glob("*.md")))
        # 2026-10-27：有旧稿 = 新一轮重写，旧段群必须作废 —— 否则 part_ok 会
        # 把旧群当断点复用，用户点了「重写」却拿到同一篇（静默不生效）。
        check("旧稿存在 → 旧段群缓存作废（重写不复用旧群）",
              not parts.exists(), parts)
        prog = ProgressManager(tmp / "data" / "state" / "progress.json")
        check("完成标记已清除", 2 not in prog.completed_chapters(4),
              prog.completed_chapters(4))

        print("\n【B】NF_STAGE4_ONLY：只写指定章")
        os.environ["NF_STAGE4_ONLY"] = "2"
        try:
            c1 = CountingClient()
            ok, msg, prog2 = run_stage4(tmp, c1)
        finally:
            os.environ.pop("NF_STAGE4_ONLY", None)
        check("只调了第 2 章", c1.main_calls == ["stage4_ch02.md"], c1.main_calls)
        check("第 2 章重新落盘", (raw / "02.md").exists())
        check("第 1 章没被重写（文件时间/内容都不该被覆盖）",
              "第1章" in (raw / "01.md").read_text(encoding="utf-8"), "ok")
        check("第 3 章没被重写", "第3章" in (raw / "03.md").read_text(encoding="utf-8"), "ok")
        check("阶段返回成功", ok is True, msg)

        print("\n【B2】旧稿不存在（上次重写崩了）→ 段群保留，供断点续跑")
        parts2 = tmp / "data" / "chapters" / "_parts" / "02"
        parts2.mkdir(parents=True, exist_ok=True)
        (parts2 / "g1.md").write_text("已完成的群" * 200, encoding="utf-8")
        (raw / "02.md").unlink()          # 模拟重写中途被杀：正文没拼装，群在盘上
        nf_api._prepare_chapter_rewrite(2)
        check("无旧稿 → 段群缓存保留（零 token 续跑的关键）",
              (parts2 / "g1.md").exists(), sorted(p.name for p in parts2.glob("*") if p.is_file()))

        print("\n【C】端点参数校验")
        from nf_api_domains import chapters as dom
        code, p = dom.handle_chapters_rewrite(None, {"n": "x"})
        check("非法章号 → 400", code == 400 and "n 必填" in p.get("error", ""), p)
        code, p = dom.handle_chapters_rewrite(None, {})
        check("缺 n → 400", code == 400, p)

        print("\n【D】前端接线")
        app_vue = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
        check("章节行有「重写」按钮", "rewriteChapter(c.n)" in app_vue)
        check("调的是 /chapters/rewrite", "/chapters/rewrite" in app_vue)
        check("有二次确认（会花 token）", "会调用模型重新写整章" in app_vue)
        check("运行中禁用（不抢 job）",
              "rewriting === c.n || isRunning" in app_vue)
        src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
        check("stage4 认 NF_STAGE4_ONLY",
              "NF_STAGE4_ONLY" in src and "只写指定章" in src)
        # 2026-10-27：单章重写只写稿 —— 不再顺带跑 review_after_stage4，
        # 否则一次重写会被后置审稿拖成半小时以上（用户实测）。
        api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        orc_src = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
        check("重写设置 NF_SKIP_POST_STAGE",
              "NF_SKIP_POST_STAGE" in api_src)
        check("orchestrator 认 NF_SKIP_POST_STAGE（跳过审稿后置钩子）",
              "NF_SKIP_POST_STAGE" in orc_src and "跳过阶段后置钩子" in orc_src)
        # 2026-10-27 实测：`if (agentRunGuard()) return;  const ok = ...` 写在同一行，
        # 重写函数**刚进去就返回**（死代码被压缩器整段丢掉，按钮点了没反应）。
        check("重写在 agent guard 之后没有提前 return（不是死代码）",
              "if (agentRunGuard()) return;\n  const ok" in app_vue,
              app_vue[app_vue.find("async function rewriteChapter"):][:120])
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)
        nf_api._set_root(ROOT)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
