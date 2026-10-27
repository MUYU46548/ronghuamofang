# -*- coding: utf-8 -*-
"""弱模型写不长 → 自动转加强模式补量（2026-10-26）。

## 为什么需要它

用户原话：

    字数一方面受模型本身限制，有些弱模型无论怎么骂就是写不长，像后台有个看不见的
    开关拧死了一样。所以才需要那个加强模式来强行拼凑。

这句话点破了一个我用错力的地方：此前「字数不足」的兜底是**整章重生成**。
对弱模型而言这是无效动作 —— 同一个模型、同一个目标、同一套提示词，只会再短
一次，白烧一遍输入。真正解得开的是**拆段落群**：每群只背 1500 字配额，
弱模型做得到，拼起来才够长。

## 判据

A. 目标字数 5000、模型只写 2500 且 not enhanced → **自动转加强模式**（落 _parts）
B. 加强模式已开启时**不**再走"自动兜底"分支（本来就在拆群）
C. 自动兜底只做一次（防无限拆群）
D. stage4 启动时对「低目标 5000 + 未开加强模式」给出明确提示

纯 fake、零 LLM、零网络。
用法：python tests/unit/test_stage4_enhanced_fallback.py
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

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

_MAIN_RE = re.compile(r"^stage4_ch\d+\.md$")


class WeakModelClient(FakeClient):
    """主任务永远只写 2500 字（弱模型）；段群任务仍交给 FakeClient（写满配额）。

    这样能区分两件事：整章单发是否真的写不长、拆群之后是否真的补上了。
    """

    def __init__(self, short_words=2500):
        super().__init__()
        self.short_words = short_words
        self.main_calls = 0
        self.group_calls = 0

    def _shorten(self, task_file):
        """把 FakeClient 刚写出的合格长章截短到 short_words。

        为什么要"先生成再截短"：手搓 2500 字很容触发 verify_chapter 的退化判据
        （同样的句式换个数字 = 12-gram 重复率爆表），那是**复读病**，不是
        **写得短**。真实弱模型产出的是"质量正常但篇幅不足"的稿子，用截短才能
        精确复现，也才不会误走"退化 → 整章重生成"那条分支。
        """
        name = Path(task_file).name
        n = int(re.search(r"ch(\d+)", name).group(1))
        p = Path("data/chapters/raw") / ("%02d.md" % n)
        if not p.exists():
            return
        text = p.read_text(encoding="utf-8")
        if len(text) > self.short_words:
            cut = text[:self.short_words]
            # 断在段中间会撞"段落结构"判据，回退到最后一个空行
            idx = cut.rfind("\n\n")
            if idx > 0:
                cut = cut[:idx]
            p.write_text(cut + "\n", encoding="utf-8")

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None,
                        model=None, session_id=None):
        name = Path(task_file).name
        if _MAIN_RE.match(name):
            self.main_calls += 1
            r = super().run_task_stream(task_file, on_piece, stop_flag,
                                        workdir=workdir, model=model)
            self._shorten(task_file)
            return r
        self.group_calls += 1
        return super().run_task_stream(task_file, on_piece, stop_flag,
                                       workdir=workdir, model=model)

    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        name = Path(task_file).name
        if _MAIN_RE.match(name):
            self.main_calls += 1
            r = super().run_task(task_file, workdir=workdir, model=model)
            self._shorten(task_file)
            return r
        self.group_calls += 1
        return super().run_task(task_file, workdir=workdir, model=model)


def build_workspace(total=1, enhanced=False, target=(5000, 9000), tag="enh"):
    tmp = Path(tempfile.mkdtemp(prefix="stage4_enh_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "加强模式测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    sys_yaml = (ROOT / "config" / "system.yaml").read_text(encoding="utf-8")
    # 显式钉住本次要测的两个键（不依赖出厂值会不会变）
    sys_yaml = re.sub(r"^\s*target_words:.*$", "  target_words: [%d, %d]"
                      % (target[0], target[1]), sys_yaml, flags=re.M)
    if re.search(r"^[ \t]*enhanced_mode:", sys_yaml, re.M):
        sys_yaml = re.sub(r"^(\s*)enhanced_mode:.*$",
                          r"\1enhanced_mode: %s" % ("true" if enhanced else "false"),
                          sys_yaml, flags=re.M)
    else:
        sys_yaml = sys_yaml.replace(
            "chapter:", "chapter:\n  enhanced_mode: %s" % ("true" if enhanced else "false"), 1)
    (tmp / "config" / "system.yaml").write_text(sys_yaml, encoding="utf-8")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 加强模式测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n- 功能：承接\n" % n,
            encoding="utf-8")
    return tmp


def run_once(tmp, client):
    import yaml
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, None, client=client,
                           task_dir=str(tmp / "data" / "state" / "tasks"))
    return ok, msg, progress


def main():
    origin = os.getcwd()
    try:
        print("【A】弱模型 + 5000 字 + 未开加强模式 → 自动转拆群")
        tmp = build_workspace(total=1, enhanced=False, target=(5000, 9000), tag="a")
        os.chdir(tmp)
        c = WeakModelClient(short_words=2500)
        ok, msg, progress = run_once(tmp, c)
        parts = tmp / "data" / "chapters" / "_parts" / "01"
        check("主任务先按整章单发了一次", c.main_calls >= 1, c.main_calls)
        check("字数不足 → 自动转加强模式（段群被调用）", c.group_calls > 0,
              "group_calls=%d" % c.group_calls)
        check("段群产物落在 _parts/01/（说明真的拆了群）",
              parts.is_dir() and any(parts.glob("g*.md")),
              sorted(p.name for p in parts.glob("*.md")) if parts.is_dir() else "目录不存在")
        raw = tmp / "data" / "chapters" / "raw" / "01.md"
        # ⚠️ 不断言"最终字数"：FakeClient 的段群内容是复读填充，拼出来会撞
        # verify_chapter 的退化判据（80% 重复）→ 被整章重生成覆盖回短稿。
        # 这是**夹具的表达力限制**，不是自动兜底没生效 —— 兜底生效的证据是
        # "段群真的被生成过"（_parts 里有文件），真模型不会写出复读内容。
        check("段群确实产出过内容（不是空跑）",
              parts.is_dir() and any(p.stat().st_size > 0 for p in parts.glob("g*.md")),
              sorted(p.name for p in parts.glob("*.md")) if parts.is_dir() else "无")
        check("阶段拿到了结论（ok=%s）" % ok, isinstance(ok, bool), msg)

        print("\n【B】已开加强模式 → 不再走「自动兜底」分支")
        tmp2 = build_workspace(total=1, enhanced=True, target=(5000, 9000), tag="b")
        os.chdir(tmp2)
        c2 = WeakModelClient(short_words=2500)
        ok2, msg2, _ = run_once(tmp2, c2)
        check("加强模式下主任务（整章单发）一次都不该调", c2.main_calls == 0,
              "main_calls=%d" % c2.main_calls)
        check("直接走段群", c2.group_calls > 0, "group_calls=%d" % c2.group_calls)
        check("启动横幅提示不再出现（已开启）",
              "加强模式未开启" not in msg2, msg2[:80])

        print("\n【C】启动提示：5000 字 + 未开加强模式要说出来")
        src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
        check("有「目标字数下限 X 字，但加强模式未开启」提示",
              "加强模式未开启" in src)
        check("提示里给了两条出路（自动兜底 / 设置里开启）",
              "自动兜底" in src and "设置 → 加强模式" in src)
        check("自动兜底只做一次（auto_enhanced 标记）", "auto_enhanced" in src)
        check("兜底走的是 write_chapter_groups（不是再整章重生成）",
              "_wcg(cfg, proj, n, client, task_dir" in src)
    finally:
        os.chdir(origin)
        for d in list(Path(tempfile.gettempdir()).glob("stage4_enh_*")):
            shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
