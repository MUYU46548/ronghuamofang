# -*- coding: utf-8 -*-
"""卷弧滚动规划的自检（2026-10-20，台账三·「卷弧双层滚动规划」）。

## 主判据

**分卷与窗口**
1. `volume_of` / `volume_range`：20 章/卷时第 21 章属第 2 卷，末卷不越界
2. `horizon`：只铺「当前卷 + 后续 roll-1 卷」；roll=0 → 全书（关闭滚动 = 老行为）
3. 写到第 45 章、roll=2 → 窗口到第 100 章（只往前看两卷，不是全书）

**卷纲质量（会失败的反证）**
4. 缺任何一章的逐章目标 → 卷纲判不合格
5. 含占位符（待补充 / TODO / XXX / 待定）→ 判不合格
6. 合格的卷纲通过

**注入**
7. 有卷纲：注入本卷目标 / 本卷转折 / 本章在卷内的位置
8. 没有卷纲：**明确说"尚未生成"**，不许静默留空（否则用户以为规划生效了）
9. 下一卷（远层）只有一句话方向；未规划就写"尚未规划"—— 不编细节

**ensure 纪律**
10. 已有且合格 → 不再调模型（0 次调用）
11. 存在但不合格 → 重新规划（这是修复，不是静默换契约）
12. 生成后仍不合格 → 返回失败，**不登记**为已规划
13. 合格 → plan.json 记录卷范围/状态/基于第几章

**stage3 滚动**
14. 窗口 < 全书时只生成窗口内章大纲，并带 `warning`（不假装全书铺完）
15. 窗口外章节由 stage4 按需补齐（`generate_chapters` 可单独生成一章）

用法：python tests/unit/test_volume_arc.py
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

from utils import volume_arc as VA                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (detail,)))


def volume_md(v, start, end, fill=True, placeholder=False):
    lines = ["# 第%d卷 门后的人（第 %d–%d 章）" % (v, start, end), "",
             "## 本卷目标", "露汐把旧案的线头拉到自己身上，付出一个名字的代价。", "",
             "## 关键转折", "- （第 %d 章附近）档案室被封" % start,
             "- （第 %d 章附近）父亲的名字出现在借阅卡上" % min(end, start + 5), "",
             "## 逐章目标"]
    for n in range(start, end + 1):
        if fill and not (placeholder and n == start + 1):
            lines.append("第 %d 章：露汐在第 %d 区的旧库房里找到一份借阅卡。" % (n, n % 7 + 1))
        elif placeholder and n == start + 1:
            lines.append("第 %d 章：待补充" % n)
    lines += ["", "## 卷末交接", "下一卷从封条被撕开的那一夜开始。"]
    return "\n".join(lines) + "\n"


class Stub:
    """最小客户端替身：按任务文本里的章号列表产出章大纲 / 卷纲。"""

    def __init__(self, volume_text=None, chapter_body=None):
        self.volume_text = volume_text
        self.chapter_body = chapter_body or "## 第N章 名字\n- 核心事件：某事发生\n- 涉及角色：露汐\n- 功能：推进\n"
        self.calls = []

    def write_task(self, task_dir, name, content):
        p = Path(task_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self.calls.append(name)
        return p

    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        text = Path(task_file).read_text(encoding="utf-8")
        if self.volume_text is not None:
            m = re.search(r"===FILE:\s*(\S+)\s*===", text)
            if m:
                Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                Path(m.group(1)).write_text(self.volume_text, encoding="utf-8")
        for m in re.finditer(r"^-\s*第(\d+)章\s*→\s*(\S+\.md)\s*$", text, re.M):
            n, rel = int(m.group(1)), m.group(2)
            Path(rel).parent.mkdir(parents=True, exist_ok=True)
            Path(rel).write_text(self.chapter_body.replace("第N章", "第%d章" % n), encoding="utf-8")
        return {"exit_code": 0, "stdout_tail": "", "tokens": 0, "tokens_out": 0,
                "cache_read": 0, "cost_yuan": 0.0}


class Sandbox:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data" / "outline" / "chapters").mkdir(parents=True)
        (self.root / "data" / "chapters" / "raw").mkdir(parents=True)
        (self.root / "data" / "setting").mkdir(parents=True)
        (self.root / "data" / "setting" / "setting.json").write_text(
            json.dumps({"characters": [{"name": "露汐"}]}, ensure_ascii=False), encoding="utf-8")
        (self.root / "data" / "outline" / "global.md").write_text(
            "# 全书大纲\n起承转合\n", encoding="utf-8")
        (self.root / "config").mkdir(parents=True)
        # prompts/ 必须一起搬：`load_template` 按 **CWD** 找模板（不是 ROOT），
        # 只建一个空工作区会让"模板缺变量"这类真问题被 FileNotFoundError 掩盖。
        # 搬的是**仓库里的真实模板** —— 测试顺带验证模板与调用方变量对得上。
        shutil.copytree(ROOT / "prompts", self.root / "prompts")
        self._cwd = os.getcwd()
        os.chdir(self.root)

    def close(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def raw(self, n):
        (self.root / "data" / "chapters" / "raw" / ("%02d.md" % n)).write_text("x", encoding="utf-8")


# ====================================================================== P1
def case_volume_math():
    print("\n[P1] 分卷与滚动窗口")
    check("P1 第 1 章属第 1 卷", VA.volume_of(1, 20) == 1)
    check("P1 第 20 章属第 1 卷", VA.volume_of(20, 20) == 1)
    check("P1 第 21 章属第 2 卷", VA.volume_of(21, 20) == 2, VA.volume_of(21, 20))
    check("P1 第 200 章属第 10 卷", VA.volume_of(200, 20) == 10)
    check("P1 末卷不越界（200 章 / 第 10 卷 → 181–200）", VA.volume_range(10, 20, 200) == (181, 200),
          VA.volume_range(10, 20, 200))
    check("P1 末卷章数不足时收在总数（205 章第 11 卷 → 201–205）",
          VA.volume_range(11, 20, 205) == (201, 205), VA.volume_range(11, 20, 205))

    check("P1 未写作 + roll=2 → 窗口到第 40 章", VA.horizon(200, 0, 20, 2) == 40,
          VA.horizon(200, 0, 20, 2))
    # 写到第 45 章 → 正写的是第 46 章 → 第 3 卷（41–60）+ 后续 1 卷 → 到第 80 章
    check("P1 写到第 45 章 + roll=2 → 窗口到第 80 章（第 3、4 卷）",
          VA.horizon(200, 45, 20, 2) == 80, VA.horizon(200, 45, 20, 2))
    check("P1 roll=0 → 全书（关闭滚动 = 老行为）", VA.horizon(200, 45, 20, 0) == 200,
          VA.horizon(200, 45, 20, 0))
    check("P1 总章数小于窗口时收在总数", VA.horizon(15, 2, 20, 2) == 15, VA.horizon(15, 2, 20, 2))

    check("P1 volume_size 缺省 20", VA.volume_size({}, {}) == VA.DEFAULT_VOLUME_SIZE)
    check("P1 project.yaml 的 volume_size 优先",
          VA.volume_size({"chapter": {"volume_size": 30}}, {"book": {"volume_size": 12}}) == 12,
          VA.volume_size({"chapter": {"volume_size": 30}}, {"book": {"volume_size": 12}}))
    check("P1 写坏（0 / abc）回落默认",
          VA.volume_size({}, {"book": {"volume_size": 0}}) == VA.DEFAULT_VOLUME_SIZE)
    check("P1 rolling_volumes 默认 2", VA.rolling_volumes({}) == 2)


def case_check_outline():
    print("\n[P2] 卷纲合格判据")
    tmp = Path(tempfile.mkdtemp())
    try:
        ok_p = tmp / "ok.md"
        ok_p.write_text(volume_md(2, 21, 40), encoding="utf-8")
        ok, probs = VA.check_volume_outline(ok_p, 21, 40)
        check("P2 完整卷纲通过", ok, probs)

        no_ch = tmp / "no_ch.md"
        no_ch.write_text(volume_md(2, 21, 40).replace("第 33 章：露汐", "第 33 章已删：露汐"),
                         encoding="utf-8")
        # 把第 33 行整行换掉，模拟"漏了一章"
        text = volume_md(2, 21, 40).splitlines()
        text = [ln for ln in text if not ln.startswith("第 33 章")]
        no_ch.write_text("\n".join(text), encoding="utf-8")
        ok2, probs2 = VA.check_volume_outline(no_ch, 21, 40)
        check("P2 缺一章逐章目标 → 不合格", not ok2, probs2)
        check("P2 不合格原因点名缺哪章", any("33" in p for p in probs2), probs2)

        ph = tmp / "ph.md"
        ph.write_text(volume_md(2, 21, 40, placeholder=True), encoding="utf-8")
        ok3, probs3 = VA.check_volume_outline(ph, 21, 40)
        check("P2 含占位符（待补充）→ 不合格", not ok3, probs3)
        check("P2 不合格原因点名占位符", any("占位符" in p for p in probs3), probs3)

        miss = tmp / "none.md"
        ok4, probs4 = VA.check_volume_outline(miss, 1, 20)
        check("P2 文件不存在 → 不合格且给出路径", not ok4 and "不存在" in probs4[0], probs4)

        parsed = VA.parse_volume_outline(volume_md(2, 21, 40))
        check("P2 解析出本卷目标", "旧案" in parsed["goal"], parsed["goal"])
        check("P2 解析出关键转折条目", len(parsed["turns"]) == 2, parsed["turns"])
        check("P2 解析出全部逐章目标", len(parsed["chapters"]) == 20, len(parsed["chapters"]))
        check("P2 解析出卷末交接", "封条" in parsed["handoff"], parsed["handoff"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_render():
    print("\n[P3] 注入块：有卷纲 / 无卷纲 / 远层只有方向")
    sb = Sandbox()
    try:
        r = VA.render_context(25, 200, {"chapter": {"volume_size": 20}}, {})
        check("P3 无卷纲时明确说「卷纲尚未生成」", "尚未生成" in r, r)
        check("P3 无卷纲时给出补救命令", "volume_plan.py" in r, r)

        p = VA.volume_path(2)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(volume_md(2, 21, 40), encoding="utf-8")
        r2 = VA.render_context(25, 200, {"chapter": {"volume_size": 20}}, {})
        check("P3 注入本卷目标", "旧案" in r2, r2)
        check("P3 注入关键转折", "档案室被封" in r2, r2)
        check("P3 注入本章在卷内的位置（卷纲原话）",
              "本章在本卷里的位置" in r2 and "借阅卡" in r2, r2)
        check("P3 远层没规划就写「尚未规划」（不编细节）", "尚未规划" in r2, r2)

        plan = VA.load_plan()
        VA.mark_outlined(plan, 3, 41, 60)
        plan["volumes"]["3"]["goal"] = "露汐开始反查自己的来历。"
        VA.save_plan(plan)
        r3 = VA.render_context(25, 200, {"chapter": {"volume_size": 20}}, {})
        check("P3 下一卷有计划时只给一句话方向",
              "露汐开始反查自己的来历" in r3 and "逐章目标" not in r3, r3)
    finally:
        sb.close()


# ====================================================================== P4
def case_ensure():
    print("\n[P4] ensure 纪律：缺才生成 / 合格不覆盖 / 不合格重规划 / 不合格不登记")
    from volume_plan import ensure_volume
    sb = Sandbox()
    try:
        cfg = {"chapter": {"volume_size": 20, "rolling_volumes": 2, "batch_outline": 10}}
        proj = {"book": {"chapters": 200}}
        stub = Stub(volume_text=volume_md(1, 1, 20))
        ok, msg, gen = ensure_volume(1, cfg, proj, client=stub, total=200, written=0)
        check("P4 首次 ensure 生成卷纲", ok and gen, msg)
        check("P4 模型被调用一次", len(stub.calls) == 1, stub.calls)
        check("P4 卷纲落盘", VA.volume_path(1).exists())
        plan = VA.load_plan()
        check("P4 plan.json 记录卷范围", plan["volumes"]["1"]["start"] == 1
              and plan["volumes"]["1"]["end"] == 20, plan["volumes"])
        check("P4 plan.json 记录状态 outlined", plan["volumes"]["1"]["status"] == "outlined")
        check("P4 plan.json 记录本卷目标（给远层一句话用）",
              "旧案" in plan["volumes"]["1"].get("goal", ""), plan["volumes"]["1"])

        stub2 = Stub(volume_text=volume_md(1, 1, 20))
        ok2, msg2, gen2 = ensure_volume(1, cfg, proj, client=stub2, total=200, written=0)
        check("P4 已存在且合格 → 不再调模型（0 次调用）",
              ok2 and not gen2 and len(stub2.calls) == 0, stub2.calls)
        check("P4 并且明说「已存在且合格」", "已存在且合格" in msg2, msg2)

        # 存在但不合格（缺章）→ 重新规划
        text = [ln for ln in volume_md(1, 1, 20).splitlines() if not ln.startswith("第 7 章")]
        VA.volume_path(1).write_text("\n".join(text), encoding="utf-8")
        stub3 = Stub(volume_text=volume_md(1, 1, 20))
        ok3, msg3, gen3 = ensure_volume(1, cfg, proj, client=stub3, total=200, written=0)
        check("P4 不合格 → 重新规划（不是静默信任）", gen3 and len(stub3.calls) == 1, stub3.calls)
        check("P4 重规划后合格", ok3, msg3)

        # 生成结果仍不合格 → 失败且不登记为 outlined
        VA.volume_path(2).unlink(missing_ok=True)
        stub4 = Stub(volume_text="# 第2卷\n\n太短了")
        ok4, msg4, gen4 = ensure_volume(2, cfg, proj, client=stub4, total=200, written=0)
        check("P4 生成结果不合格 → 返回失败", not ok4, msg4)
        check("P4 不合格原因说清楚", "不合格" in msg4, msg4)
        check("P4 不合格**不登记**到 plan.json",
              "2" not in (VA.load_plan().get("volumes") or {}), VA.load_plan().get("volumes"))
        check("P4 失败文件留在盘上（可手工改，不删证据）", VA.volume_path(2).exists())
    finally:
        sb.close()


def case_stage3_rolling():
    print("\n[P5] stage3 只铺窗口内；窗口外可单章补齐")
    import stage3_chapter_outline as S3
    from utils.progress_manager import ProgressManager

    sb = Sandbox()
    try:
        cfg = {"chapter": {"volume_size": 20, "rolling_volumes": 2, "batch_outline": 10}}
        proj = {"book": {"chapters": 200}}
        stub = Stub()
        progress = ProgressManager("data/state/progress.json")
        ok, msg = S3.run_stage(cfg, proj, progress, None, None, client=stub, task_dir="data/state/tasks")
        made = sorted(int(p.stem) for p in Path("data/outline/chapters").glob("*.md"))
        check("P5 只生成窗口内（40 章），不是全书 200 章", made == list(range(1, 41)), made[:5])
        check("P5 返回消息说明是窗口内", "窗口" in msg or "滚动" in msg, msg)
        st = (progress.data.get("stages") or {}).get("3") or {}
        check("P5 progress 带 warning（不假装全书铺完）",
              "滚动" in (st.get("warning") or ""), st)

        # 窗口外第 41 章：stage4 走 generate_chapters 单章补齐
        stub2 = Stub()
        ok2, msg2 = S3.generate_chapters(cfg, proj, [41], stub2, "data/state/tasks")
        check("P5 单章补齐能生成第 41 章大纲", ok2 and Path("data/outline/chapters/41.md").exists(),
              msg2)
        check("P5 补齐走的是同一份实现（任务名 stage3_batch）",
              any(c.startswith("stage3_batch") for c in stub2.calls), stub2.calls)

        # 章大纲带上了卷弧上下文
        t = next(Path("data/state/tasks").glob("stage3_batch*.md")).read_text(encoding="utf-8")
        check("P5 章大纲任务里有卷弧块（不是只有全书大纲路径）", "卷弧" in t, t[:200])
    finally:
        sb.close()


def main():
    print("=" * 62)
    print("  卷弧滚动规划自检（docs/收口台账-20261019.md 三·卷弧双层滚动）")
    print("=" * 62)
    case_volume_math()
    case_check_outline()
    case_render()
    case_ensure()
    case_stage3_rolling()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
