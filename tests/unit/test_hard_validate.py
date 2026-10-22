# -*- coding: utf-8 -*-
"""第一波防御 · 任务一验收（字数与标点硬校验，纯 fake，零 LLM）。

## 施工单验收线（2026-10-22）

> 占位素材试产 5 章，人为混入 2 章字数不足、1 章标点错误——三章必须全部被拦下
> 并留痕；2 章正常章不受误伤。

## 本用例的对应

1. 标点判据单元测试：不成对配对（硬拦）、连续重复标点（硬拦）、双引号计数差
   （只警告）、保护区跳过（注释/围栏不误报）。
2. 归一化后仍残留的形态才算错（text_norm 修得掉的不该由这里拦）。
3. 5 章试产验收：2 短章 + 1 标点章 → 全部停在「待人工」；留痕 JSON 齐全
   （字数/问题清单/处置/打回轮次）；2 个正常章一次过、零额外调用。
4. 防误伤：正常章不因新判据多烧一次调用；跨段引语/句末留白不判错。

用法：python tests/unit/test_hard_validate.py
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
from utils.punct_check import check_punctuation           # noqa: E402
from utils.verify_chapter import check_chapter            # noqa: E402
from utils.progress_manager import ProgressManager        # noqa: E402
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
_PH = ("<!-- summary: 第%d章测试摘要 -->\n<!-- quality: 8/10 -->")


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


def _chapter(n, target_chars, seed_base=7, tail=""):
    return ("## 第%d章 测试章\n\n" % n) + _gen_body(target_chars, seed_base + n) \
        + tail + "\n\n" + (_PH % n) + "\n"


def _patch_cfg(sys_yaml, low, high, regen_rounds=2):
    """钉死字数区间与打回上限（夹具自带口径，不随仓库配置漂移）。

    ⚠️ 行内列表（`target_words: [5000, 9000]`）与旧块序列两种形态都要吃：
    出厂配置 2026-10-18 起是行内形态，只匹配块序列会让夹具静默用上仓库真值
    （本用例首轮就这么假红的）。
    """
    lines = Path(sys_yaml).read_text(encoding="utf-8").split("\n")
    has_regen = any(l.strip().startswith("hard_validate_regen_rounds:") for l in lines)
    out, patched = [], False
    for ln in lines:
        if ln.strip().startswith("target_words:"):
            out.append("  target_words: [%d, %d]" % (low, high))
            patched = True
            continue
        if ln.strip().startswith("hard_validate_regen_rounds:"):
            out.append("  hard_validate_regen_rounds: %d" % regen_rounds)   # 替换不重复
            continue
        out.append(ln)
        if not has_regen and ln.rstrip() == "gates:":
            out.append("  hard_validate_regen_rounds: %d" % regen_rounds)
    if not patched:
        out.append("chapter:")
        out.append("  target_words: [%d, %d]" % (low, high))
    Path(sys_yaml).write_text("\n".join(out), encoding="utf-8")


def build_workspace(total=5, regen_rounds=2):
    tmp = Path(tempfile.mkdtemp(prefix="hardval_"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "防御测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    _patch_cfg(tmp / "config" / "system.yaml", 2000, 3000, regen_rounds)
    (tmp / "materials" / "raw").mkdir(parents=True)
    for sub in ("setting", "outline/chapters", "state"):
        (tmp / "data" / sub).mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 防御测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
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


# ---------------------------------------------------------------- 单元：标点判据
def case_punct_unit():
    print("\n【1】标点判据单元（硬拦两类 / 警告两类 / 保护区）")
    good = _chapter(1, 2400)
    errs, warns, _ = check_punctuation(good)
    check("正常章零硬拦", errs == [], errs)
    check("正常章句末无标点不报警（每段都以句号收尾）",
          not any("句末" in w for w in warns), warns)

    bad_pair = good + "\n\n他翻开《残卷，又随手合上。\n"
    errs, _, _ = check_punctuation(bad_pair)
    check("不成对书名号 → 硬拦", any("书名号" in e for e in errs), errs)

    bad_rep = good + "\n\n她愣住了。。他也是。\n"
    errs, _, _ = check_punctuation(bad_rep)
    check("连续重复标点 → 硬拦", any("连续重复" in e for e in errs), errs)

    dbl = good + "\n\n“第一段跨段引语。\n\n“第二段才收尾。”\n"
    errs, warns, _ = check_punctuation(dbl)
    check("跨段双引语（开2合1）不硬拦（合法排版）",
          not any("双引号" in e for e in errs), errs)
    check("跨段双引语进警告（请人复核）",
          any("双引号" in w for w in warns), warns)

    # 保护区：注释块里的 ASCII 括号/引号不误报
    protected = good + "\n\n<!-- (unbalanced [quote -->\n```\n（代码里的括号\n```\n"
    errs, _, _ = check_punctuation(protected)
    check("注释块/代码围栏不参与校验（零误报）", errs == [], errs)


def case_punct_after_norm():
    print("\n【2】归一化分工：text_norm 修得掉的不进硬拦，修不掉的才拦")
    from utils.text_norm import normalize_chinese_punctuation
    # 直角引号 + 半角引号 + 重复句号：归一化应全部修掉 → 校验零硬拦
    dirty = "## 第1章\n\n" + _gen_body(2200, 3) + "\n\n他说「嗯」，她答\"好\"。。走吧。\n"
    clean = normalize_chinese_punctuation(dirty)
    errs, _, _ = check_punctuation(clean)
    check("归一化产物零硬拦（「」/\"/。。 都已被修）", errs == [], errs)
    # 未归一化直接写入的不成对书名号：归一化不管（它只管引号家族）→ 仍被拦
    errs2, _, _ = check_punctuation(dirty + "\n\n《未闭合的书名\n")
    check("归一化不管的形态（不成对《》）仍被拦", any("书名号" in e for e in errs2), errs2)


# ---------------------------------------------------------------- 验收：5 章试产
class AcceptanceClient:
    """第 2/4 章恒短稿（扩写/重生成也短）；第 5 章恒带不成对书名号；其余正常。"""

    SHORT_CHARS = 1300     # >= 0.6*2000 → 够扩写门槛

    def __init__(self):
        self.calls = []

    def write_task(self, task_dir, name, content):
        Path(task_dir).mkdir(parents=True, exist_ok=True)
        p = Path(task_dir) / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def run_task(self, task_path, **kwargs):
        name = Path(task_path).name
        self.calls.append(name)
        m = re.search(r"stage4_ch(\d+)", name)
        n = int(m.group(1))
        raw = Path("data/chapters/raw")
        raw.mkdir(parents=True, exist_ok=True)
        if n in (2, 4):
            body = _chapter(n, self.SHORT_CHARS)
        elif n == 5:
            body = _chapter(n, 2400, tail="\n\n他翻开《残卷，又随手合上。\n")
        else:
            body = _chapter(n, 2400)
        (raw / ("%02d.md" % n)).write_text(body, encoding="utf-8")
        return {"exit_code": 0, "tokens": 10, "tokens_out": 5, "text": "",
                "model": "fake"}


def case_acceptance_5chapters():
    print("\n【3】验收线：5 章试产，混 2 短章 + 1 标点章 → 三章全拦 + 留痕，正常章零误伤")
    tmp = build_workspace(total=5)
    origin = os.getcwd()
    try:
        c = AcceptanceClient()
        ok, msg, progress = run_stage4(tmp, c)

        # ---- 三章坏章全部拦下（停在待人工 = failed 章）----
        failed_ns = sorted(f["n"] for f in progress.failed_chapters(4))
        check("2 短章 + 1 标点章全被拦下", failed_ns == [2, 4, 5], failed_ns)
        check("阶段判失败（不产出成品）", ok is False, msg)

        # ---- 正常章零误伤：一次过、标 done ----
        done_ns = progress.completed_chapters(4)
        check("正常章 1、3 通过且标 done", done_ns == [1, 3], done_ns)
        calls_1 = [x for x in c.calls if re.search(r"stage4_ch01", x)]
        calls_3 = [x for x in c.calls if re.search(r"stage4_ch03", x)]
        check("正常章 1 只写一次（零额外调用 = 零误伤）", len(calls_1) == 1, calls_1)
        check("正常章 3 只写一次", len(calls_3) == 1, calls_3)

        # ---- 打回轮次：短章 = 扩写1轮 + 重生成1轮；标点章 = 重生成2轮 ----
        calls_2 = [x for x in c.calls if re.search(r"stage4_ch02", x)]
        calls_5 = [x for x in c.calls if re.search(r"stage4_ch05", x)]
        exp2 = [x for x in calls_2 if "_expand" in x]
        check("短章走了扩写 + 重生成（共 3 次调用）", len(calls_2) == 3 and len(exp2) == 1,
              calls_2)
        check("标点章走 2 轮整章重生成（共 3 次调用）", len(calls_5) == 3, calls_5)

        # ---- 留痕（任务一硬要求）----
        from utils.validation_log import load_summary, load_chapter_log, manual_chapters
        summary = load_summary()
        check("5 章留痕齐全", sorted(summary.keys()) == ["01", "02", "03", "04", "05"],
              sorted(summary.keys()))
        check("正常章处置 = passed", summary["01"]["disposition"] == "passed"
              and summary["03"]["disposition"] == "passed", summary["01"])
        check("坏章处置 = regenerated（待人工）",
              all(summary[k]["disposition"] == "regenerated" for k in ("02", "04", "05")),
              {k: summary[k]["disposition"] for k in ("02", "04", "05")})
        check("manual_chapters() 恰为 [2,4,5]", manual_chapters() == [2, 4, 5],
              manual_chapters())

        log2 = load_chapter_log(2)
        check("短章留痕：字数/轮次/动作齐全",
              log2["word_count"] < 2000 and log2["regen_rounds"] == 2
              and log2["actions"] == ["扩写第1轮", "重生成第2轮"], log2)
        check("短章留痕：问题清单含字数区间",
              any("字数" in e for e in log2["errors"]), log2["errors"][:2])
        log5 = load_chapter_log(5)
        check("标点章留痕：标点问题在册",
              any("标点" in e for e in log5["errors"]), log5["errors"][:3])
        check("标点章留痕：punct_errors 单独可读",
              bool(log5["punct_errors"]), log5["punct_errors"])
        log1 = load_chapter_log(1)
        check("正常章留痕：0 轮打回、无标点错误",
              log1["regen_rounds"] == 0 and log1["punct_errors"] == [], log1)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_zero_regen_compat():
    print("\n【4】hard_validate_regen_rounds=0 → 退回改动前行为（不过即失败，不打回）")
    tmp = build_workspace(total=2, regen_rounds=0)
    origin = os.getcwd()
    try:
        c = AcceptanceClient()
        ok, _, progress = run_stage4(tmp, c)
        calls_2 = [x for x in c.calls if re.search(r"stage4_ch02", x)]
        check("短章只写一次（无打回）", len(calls_2) == 1, calls_2)
        check("短章判失败", any(f["n"] == 2 for f in progress.failed_chapters(4)))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    t0 = time.time()
    for fn in (case_punct_unit, case_punct_after_norm,
               case_acceptance_5chapters, case_zero_regen_compat):
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
