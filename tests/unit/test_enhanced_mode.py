# -*- coding: utf-8 -*-
"""第一波防御 · 任务三验收（加强模式，纯 fake，零 LLM）。

## 施工单验收线（2026-10-22）

> 同一目标字数，加强模式开/关各跑 3 章（占位素材）：开启后字数达标率显著
> 高于关闭；关闭的 3 章与改动前无差异。

## 本用例的对应

1. **关闭 = 改动前行为**：enhanced_mode 缺失/false 时，整章单发、不产段群
   目录、LLM 调用形态与改动前逐字一致（1 章 1 次调用）。
2. **开启 = 拆群**：每章拆 2+ 群、逐群落盘、拼装成章、整章过同一套
   check_chapter（标题唯一 / 注释唯一 / 字数区间 / 非退化）。
3. **达标率对比**：用「整章必短、段群按配额足量」的夹具模拟真实短板
   （模型整章写 5000+ 容易尾段塌方，拆群后每群目标小）——
   关闭 3 章全灭，开启 3 章全过。
4. **段群级断点续跑**：杀在第 2 群之后 → 重启只补缺的群，
   已合格的群零重复（任务二「已完成段落落盘」在加强模式下的形态）。
5. **plan_groups 配额守恒**：各群配额之和 == 章节目标字数（施工单原话）。
6. **API 端点**：GET 回显现状、POST 严格布尔校验（"true" 字符串被拒）。

用法：python tests/unit/test_enhanced_mode.py
"""
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.verify_chapter import check_chapter            # noqa: E402
from utils.progress_manager import ProgressManager        # noqa: E402
from utils.enhanced_chapter import (plan_groups, part_path, parts_dir,  # noqa: E402
                                    clear_parts, part_ok)
import stage4_writing as s4                               # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


SETTING = {
    "characters": [{"id": "luxi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [], "_meta": {"version": 1},
}

_POOL = ("他她它的是了在有人不来去上下天地山水风雨云光暗门窗桌椅灯火茶酒"
         "剑刀机关齿轮锁链绳索封印阵法符文石砖瓦木铜铁银金布衣裘袍靴帽")


def _gen_body(target_chars, seed):
    rng = random.Random(seed)
    paras, total, i = [], 0, 0
    while total < target_chars:
        i += 1
        n = rng.randint(180, 260)
        s = "第%d段" % i + "".join(rng.choice(_POOL) for _ in range(n))
        paras.append(s)
        total += len(s)
    return "\n\n".join(paras)


def _patch_cfg(sys_yaml, low, high, enhanced):
    lines = Path(sys_yaml).read_text(encoding="utf-8").split("\n")
    out, has_enh = [], False
    for ln in lines:
        s = ln.strip()
        if s.startswith("target_words:"):
            out.append("  target_words: [%d, %d]" % (low, high))
            continue
        if s.startswith("enhanced_mode:"):
            out.append("  enhanced_mode: %s" % ("true" if enhanced else "false"))
            has_enh = True
            continue
        if s.startswith("enhanced_group_words:"):
            out.append("  enhanced_group_words: 1500")
            continue
        out.append(ln)
    if not has_enh:
        out.append("  enhanced_mode: %s" % ("true" if enhanced else "false"))
    Path(sys_yaml).write_text("\n".join(out), encoding="utf-8")


def build_workspace(total=3, enhanced=False, low=2000, high=3000):
    tmp = Path(tempfile.mkdtemp(prefix="enhmode_"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "加强模式测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    _patch_cfg(tmp / "config" / "system.yaml", low, high, enhanced)
    (tmp / "materials" / "raw").mkdir(parents=True)
    for sub in ("setting", "outline/chapters", "state"):
        (tmp / "data" / sub).mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 加强模式测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n" % n,
            encoding="utf-8")
    return tmp


def run_stage4(tmp, client):
    import yaml
    os.chdir(tmp)
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, None,
                           client=client, task_dir=str(tmp / "data" / "state" / "tasks"))
    return ok, msg, progress


class _Base:
    """整章任务恒短稿（1300 字，模拟"整章容易尾段塌方"）；
    段群任务按任务文本里的字数区间足量产出（模拟"拆群后每群目标小、易达标"）。"""

    def __init__(self):
        self.calls = []

    def write_task(self, task_dir, name, content):
        Path(task_dir).mkdir(parents=True, exist_ok=True)
        p = Path(task_dir) / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def _respond(self, name, text):
        n = int(re.search(r"stage4_ch(\d+)", name).group(1))
        is_group = "_g" in name
        mm = re.search(r"字数 (\d+)-(\d+) 字", text)
        low = int(mm.group(1)) if mm else 1300
        chars = low if is_group else 1300
        out = re.search(r"写入文件: ([^ \n]+[.]md)", text)
        p = Path(out.group(1)) if out else Path("data/chapters/raw") / ("%02d.md" % n)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 段群第 1 群带标题、最后一群带注释（模板要求）；这里模拟真模型按模板走。
        # 组装器同时要扛住 FakeClient 式"每群都带"的形态（见 case_on_assembly 注释）。
        if not is_group:
            body = ("## 第%d章 测试章\n\n" % n) + _gen_body(chars, 7 + n) \
                + "\n\n<!-- quality: 8/10 -->\n<!-- summary: 测试摘要 -->\n"
        else:
            g = int(re.search(r"_g(\d+)\.md", name).group(1))
            body = _gen_body(chars, 7 + n * 10 + g)
            if g == 1:
                body = ("## 第%d章 测试章\n\n" % n) + body
            body += "\n\n<!-- quality: 8/10 -->\n<!-- summary: 测试摘要 -->\n"
        p.write_text(body, encoding="utf-8")
        return {"exit_code": 0, "tokens": 10, "tokens_out": 5, "text": "",
                "model": "fake"}

    def run_task(self, task_path, **kw):
        name = Path(task_path).name
        self.calls.append(name)
        return self._respond(name, Path(task_path).read_text(encoding="utf-8"))


def case_off_zero_impact():
    print("\n【1】关闭 = 改动前行为（整章单发、无段群目录、1 章 1 次调用）")
    tmp = build_workspace(total=3, enhanced=False)
    origin = os.getcwd()
    try:
        c = _Base()
        ok, msg, progress = run_stage4(tmp, c)
        check("阶段照常失败（夹具整章恒短稿，走打回后仍短）",
              ok is False, msg)
        check("无段群目录产出", not (tmp / "data/chapters/_parts").exists()
              or not any((tmp / "data/chapters/_parts").glob("*")))
        # 每章：首写 1 次 + 打回 2 轮（扩写 1 + 重生成 1）= 3 次，无 _g 任务
        g_tasks = [x for x in c.calls if "_g" in x]
        check("零段群任务", g_tasks == [], g_tasks)
        per_ch = {}
        for x in c.calls:
            n = int(re.search(r"ch(\d+)", x).group(1))
            per_ch[n] = per_ch.get(n, 0) + 1
        check("每章 3 次调用（首写+扩写+重生成，与关闭拆群无关）",
              all(v == 3 for v in per_ch.values()), per_ch)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_on_assembly():
    print("\n【2】开启 = 拆群拼装：整章过同一套校验（标题唯一/注释唯一/字数区间）")
    tmp = build_workspace(total=2, enhanced=True)
    origin = os.getcwd()
    try:
        c = _Base()
        ok, msg, progress = run_stage4(tmp, c)
        raw = tmp / "data/chapters/raw"
        for n in (1, 2):
            f = raw / ("%02d.md" % n)
            check(f"第{n}章成章落盘", f.exists())
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8")
            titles = re.findall(r"^## 第\d+章.*$", text, re.M)
            check(f"第{n}章标题唯一（组装去重生效）", len(titles) == 1, titles)
            quals = re.findall(r"<!--\s*quality:.*?-->", text)
            check(f"第{n}章 quality 注释唯一", len(quals) == 1, quals)
            ck = check_chapter(f, 2000, 3000)
            check(f"第{n}章过整章校验（同一套判据）", ck.ok, ck.errors[:3])
            pd = tmp / "data/chapters/_parts" / ("%02d" % n)
            parts = sorted(pd.glob("g*.md"))
            check(f"第{n}章段群落盘（>=2 群）", len(parts) >= 2,
                  [p.name for p in parts])
        g_tasks = [x for x in c.calls if re.search(r"_g\d\.md$", x)]
        check("每章每群一次调用（2 章 × 2 群 = 4 个段群任务）",
              len(g_tasks) == 4, g_tasks)
        check("两章全部完成", progress.completed_chapters(4) == [1, 2],
              progress.completed_chapters(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_pass_rate_comparison():
    print("\n【3】验收线主体：同一目标字数，开/关各 3 章 —— 关闭全灭、开启全过")
    # 关闭
    tmp_off = build_workspace(total=3, enhanced=False)
    # 开启
    tmp_on = build_workspace(total=3, enhanced=True)
    origin = os.getcwd()
    try:
        c_off = _Base()
        ok_off, _, prog_off = run_stage4(tmp_off, c_off)
        pass_off = prog_off.completed_chapters(4)
        c_on = _Base()
        ok_on, _, prog_on = run_stage4(tmp_on, c_on)
        pass_on = prog_on.completed_chapters(4)
        check("关闭：3 章全不达标（整章恒短稿）",
              ok_off is False and pass_off == [], pass_off)
        check("开启：3 章全达标（拆群足量）",
              ok_on is True and pass_on == [1, 2, 3], pass_on)
        check("达标率 0/3 → 3/3（显著提升）",
              len(pass_on) - len(pass_off) == 3)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp_off, ignore_errors=True)
        shutil.rmtree(tmp_on, ignore_errors=True)


def case_group_checkpoint():
    print("\n【4】段群级断点续跑：杀在第 2 群后 → 重启只补缺的群")
    tmp = build_workspace(total=1, enhanced=True)
    origin = os.getcwd()
    try:
        c = _Base()
        os.chdir(tmp)
        import yaml
        cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
        proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
        from utils.enhanced_chapter import write_chapter_groups
        groups = plan_groups(2000, 3000, 1500)
        killed = {"n": 0}

        def kill_after_two(tp):
            r = c.run_task(tp)
            killed["n"] += 1
            if killed["n"] >= 2:
                raise KeyboardInterrupt("模拟进程被杀（第 2 群之后）")
            return r

        try:
            write_chapter_groups(cfg, proj, 1, c, str(tmp / "data/state/tasks"),
                                 "data/outline/chapters/01.md", [],
                                 run_hook=kill_after_two)
        except KeyboardInterrupt:
            pass
        pd = parts_dir(1)
        done_parts = sorted(p.name for p in pd.glob("g*.md") if part_ok(p, 1250))
        check("强杀后 2 群已落盘且合格", len(done_parts) == 2, done_parts)

        # ---- 重启续跑：合格群跳过，只补缺的群 ----
        before = set(c.calls)
        seen_groups = []
        agg = write_chapter_groups(cfg, proj, 1, c, str(tmp / "data/state/tasks"),
                                   "data/outline/chapters/01.md", [],
                                   on_group=lambda g, total: seen_groups.append(
                                       (g["idx"], total, g["quota"])))
        new_calls = [x for x in c.calls if x not in before]
        total_groups = len(groups)
        check("续跑只调用缺失的群（零重复）",
              len(new_calls) == total_groups - 2, (new_calls, total_groups))
        check("整章拼装落盘", (tmp / "data/chapters/raw/01.md").exists())
        # 2026-10-27：GUI 进度条要「第 g/G 群 · 本群配额」。回调必须每群都发
        # （包括已在盘、被跳过的群 —— 否则续跑时界面从 0 开始，看不出进度）。
        check("on_group 每群回调（序号/总数/配额齐全）",
              seen_groups == [(g["idx"], total_groups, g["quota"]) for g in groups],
              seen_groups)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_plan_groups():
    print("\n【5】plan_groups：配额之和 == 章节目标字数（施工单原话）")
    for low, high in ((2000, 3000), (5000, 9000), (8000, 8000)):
        groups = plan_groups(low, high, 1500)
        total = sum(g["quota"] for g in groups)
        check("目标 [%d,%d]：配额和 %d == 中值 %d" % (low, high, total, (low + high) // 2),
              total == (low + high) // 2, [g["quota"] for g in groups])
        check("目标 [%d,%d]：群数在 2~8 且递增编号" % (low, high),
              2 <= len(groups) <= 8 and [g["idx"] for g in groups]
              == list(range(1, len(groups) + 1)))
    # 极小目标也要至少 2 群（一群就不叫"拆"了）
    check("极小目标仍拆 >=2 群", len(plan_groups(500, 600, 1500)) >= 2)


def case_api_endpoint():
    print("\n【6】API 端点：GET 回显 / POST 严格布尔（字符串 \"true\" 被拒）")
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    proj_mod = importlib.import_module("nf_api_domains.project")
    ok, msg, v = proj_mod._validate_enhanced_mode({"enabled": True})
    check("布尔 true 放行", ok is True and v is True)
    ok, msg, v = proj_mod._validate_enhanced_mode({"enabled": "true"})
    check("字符串 \"true\" 拒绝（防静默当真值）", ok is False and "布尔" in msg, msg)
    ok, msg, v = proj_mod._validate_enhanced_mode({})
    check("缺 enabled 拒绝", ok is False)
    ok, msg, v = proj_mod._validate_enhanced_mode({"enabled": 1})
    check("整数 1 拒绝（严格布尔）", ok is False)


def case_file_progress_and_stop():
    print("\n【7】文件进度 + 停止传播（2026-10-27：hermes 写文件时界面不再瞎）")
    tmp = build_workspace(total=1, enhanced=True, low=2000, high=3000)
    origin = os.getcwd()
    try:
        os.chdir(tmp)
        import yaml
        cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
        proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
        from utils.enhanced_chapter import write_chapter_groups

        # 1) 每群产出后回调真实字数/字节（hermes 的 text 事件不是正文）
        c = _Base()
        seen = []
        write_chapter_groups(cfg, proj, 1, c, str(tmp / "data" / "state" / "tasks"),
                             "data/outline/chapters/01.md", [],
                             on_file_progress=lambda w, b, g: seen.append((w, b, g["idx"])))
        check("每群产出后回报文件字数/字节", seen and all(w > 0 and b > 0 for w, b, _ in seen),
              seen[:4])
        want = [g["idx"] for g in plan_groups(2000, 3000, 1500)]
        check("回报覆盖实际拆群（序号齐全）", sorted({i for _, _, i in seen}) == want, seen)

        # 2) stopped 必须原样传出去，且不再重试/开下一群
        class _StopClient(_Base):
            def run_task(self, task_path, **kw):
                self.calls.append(Path(task_path).name)
                return {"exit_code": 0, "tokens": 1, "tokens_out": 1,
                        "stopped": True, "model": "fake"}

        clear_parts(1)
        c2 = _StopClient()
        agg = write_chapter_groups(cfg, proj, 1, c2, str(tmp / "data" / "state" / "tasks"),
                                   "data/outline/chapters/01.md", [])
        check("段群 stopped 原样传出", agg.get("stopped") is True, agg)
        check("停止后只调 1 次（不重试、不跑下一群）", len(c2.calls) == 1, c2.calls)

        # 3) 群与群之间也认停止（should_stop）
        clear_parts(1)
        c3 = _Base()
        agg2 = write_chapter_groups(
            cfg, proj, 1, c3, str(tmp / "data" / "state" / "tasks"),
            "data/outline/chapters/01.md", [], should_stop=lambda: True)
        check("should_stop 在群间生效（一次都不调）", agg2.get("stopped") is True and c3.calls == [],
              c3.calls)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    t0 = time.time()
    for fn in (case_plan_groups, case_off_zero_impact, case_on_assembly,
               case_pass_rate_comparison, case_group_checkpoint, case_api_endpoint,
               case_file_progress_and_stop):
        fn()
    print("\n==== 结果: PASS %d / FAIL %d （%.1fs）===="
          % (len(PASS), len(FAIL), time.time() - t0))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
