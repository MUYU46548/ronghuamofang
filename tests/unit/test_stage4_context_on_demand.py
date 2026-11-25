# -*- coding: utf-8 -*-
"""P0-2 上下文按需读（2026-11-25）。

## 为什么有这条用例

施工任务书 §2 的判据是“单章输入 token ≤ 30,000”“任务书正文 ≤ 8 KB”，
基线是单章输入 105,579（`docs/盘点-跑书试跑-20261125.md`）。

真跑才拿得到 token 数，但**大头是可离线判的**：任务书正文里早就把本章相关的
角色卡 / 地点 / 势力 / 时间线 / 情节碎片都内联了，模板那一行却仍然写着
“设定集: <data/setting/setting.json>（仅参考涉及本章的…）”—— 模型照着一整本读。
真实设定集 126 KB，等于同一份料喂两遍，第二遍还是全本。

改法（`_write_setting_excerpt`）：落一份只含本章条目的摘录，
`{{path_setting}}` 指向它 —— `prompts/**` 一个字不动。

## 本用例判什么

1. 任务书里**不再出现** setting.json 的绝对路径，改成 `chNN_setting.md`。
2. 摘录只含本章命中条目（无关角色一条都不许有），且**显著小于**原设定集。
3. 本章没命中任何条目时**退回原设定集** —— 不给空文件（模型会以为读到了设定集）。

用法：python tests/unit/test_stage4_context_on_demand.py
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

import stage4_writing as s4                              # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _big_setting():
    """一本“很大但本章只用得上一个人”的设定集。"""
    filler = ("此人常年在城南码头上替人补网，手上有一道旧疤，说话前先咳一声。"
              "他不识字，记账靠绳子打结，谁欠他什么只有他自己看得懂。"
              "遇到生人先退半步，问清来意才肯让座。")
    chars = [{"id": "suzhi", "name": "苏芷", "role": "主角",
              "traits": ["冷静", "记性好"],
              "desc": "药铺学徒，能凭气味分辨三十几种草药。",
              "body": "苏芷是药铺学徒，能凭气味分辨三十几种草药。" + filler}]
    for i in range(1, 41):
        chars.append({"id": "extra%02d" % i, "name": "路人甲%02d" % i,
                      "role": "次要", "traits": ["沉默"],
                      "desc": "与本故事无关的填充角色。",
                      "body": "路人甲%02d 是本轮夹具用来撑体积的无关条目。" % i + filler * 3})
    return {
        "characters": chars,
        "world": {"locations": [{"name": "南城药铺", "type": "地点",
                                 "desc": "卖草药的小铺子。" + filler}],
                  "factions": [], "magic_system": [], "items": []},
        "plot_fragments": [{"summary": "苏芷在药铺后巷捡到一只会说话的匣子。",
                            "status": "unused"}],
        "timeline": [{"year": 940, "event": "苏芷入药铺当学徒。", "locked": True}],
        "_meta": {"version": 1},
    }


def build_workspace():
    tmp = Path(tempfile.mkdtemp(prefix="stage4_ctx_"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "按需读测试书"\n  chapters: 2\n', encoding="utf-8")
    shutil.copyfile(ROOT / "config" / "system.yaml",
                    tmp / "config" / "system.yaml")
    for d in ("materials/raw", "data/setting", "data/outline/chapters",
              "data/state", "data/summaries"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(_big_setting(), ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 按需读测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    # 第 1 章点名苏芷 → 应当命中；第 2 章谁都不提 → 应当退回整本设定集
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(
        "# 第1章 药铺\n\n- 核心事件：苏芷在南城药铺盘药\n- 涉及角色：苏芷（主角）\n",
        encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "02.md").write_text(
        "# 第2章 空白\n\n- 核心事件：某个与设定集无关的过场\n- 涉及角色：（无）\n",
        encoding="utf-8")
    (tmp / "data" / "summaries" / "rolling.md").write_text(
        "# 滚动摘要\n\n## 全书\n（空）\n", encoding="utf-8")
    return tmp


def build_task(tmp, n):
    import yaml
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    return s4.build_chapter_task(
        cfg, proj, n,
        "data/outline/chapters/%02d.md" % n,
        "data/setting/setting.json",
        "data/summaries/rolling.md",
        [])


def main():
    tmp = build_workspace()
    old = os.getcwd()
    try:
        os.chdir(tmp)
        setting_abs = str((tmp / "data" / "setting" / "setting.json").resolve())
        raw_bytes = (tmp / "data" / "setting" / "setting.json").stat().st_size

        # ---- 1. 命中的章：换成摘录 ----
        print("\n【1】本章命中条目 → 任务书指向摘录，不再指向整本设定集")
        task = build_task(tmp, 1)
        check("任务书里不再出现 setting.json 的绝对路径",
              setting_abs not in task, setting_abs)
        check("改成指向 data/state/context/ch01_setting.md",
              "ch01_setting.md" in task)
        ex = tmp / "data" / "state" / "context" / "ch01_setting.md"
        check("摘录文件真的落盘", ex.exists(), str(ex))
        if ex.exists():
            text = ex.read_text(encoding="utf-8")
            ex_bytes = ex.stat().st_size
            check("摘录里本章角色在（苏芷）", "苏芷" in text)
            check("无关角色一条都没有（路人甲07 不在）", "路人甲07" not in text)
            check("摘录显著小于原设定集（< 10%%：%d / %d 字节）"
                  % (ex_bytes, raw_bytes), ex_bytes < raw_bytes * 0.10,
                  "%.1f%%" % (100.0 * ex_bytes / max(raw_bytes, 1)))
            check("摘录不是空壳（>= 200 字节）", ex_bytes >= 200, ex_bytes)
        print("  任务书正文 %d 字节（原设定集 %d 字节）" % (len(task.encode("utf-8")), raw_bytes))

        # ---- 1b. 决策收件箱（P1-4）：任务书里给了路径，但**不要求**必有 ----
        print("\n【1b】决策收件箱：任务书给路径，并写明“没有就不要建文件”")
        check("任务书里有 data/canon/_inbox/01.md",
              "data/canon/_inbox" in task.replace("\\\\", "/")
              or "_inbox" in task, task[-400:])
        check("写明了“没有就不要建这个文件”", "不要建这个文件" in task)

        # ---- 2a. 本章没点名角色，但设定集里有“未启用候选素材” ----
        print("\n【2a】本章没点名角色 → 仍带候选素材兜底（不退回整本）")
        task2 = build_task(tmp, 2)
        ex2 = tmp / "data" / "state" / "context" / "ch02_setting.md"
        check("照样给摘录（候选素材兜底，不是空文件）", ex2.exists(), str(ex2))
        if ex2.exists():
            check("摘录里点明是候选素材",
                  "未启用" in ex2.read_text(encoding="utf-8"),
                  ex2.read_text(encoding="utf-8")[:160])
        check("任务书指向摘录而不是整本", setting_abs not in task2)

        # ---- 2b. 设定集完全空 → 退回整本（不给空文件） ----
        print("\n【2b】设定集没有任何条目 → 退回整本设定集，不写空摘录")
        empty = tmp / "data" / "setting" / "empty.json"
        empty.write_text(json.dumps({"characters": [], "world": {},
                                     "plot_fragments": [], "timeline": []},
                                    ensure_ascii=False), encoding="utf-8")
        import yaml
        cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
        proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
        (tmp / "data" / "state" / "context" / "ch03_setting.md").unlink(missing_ok=True)
        task3 = s4.build_chapter_task(
            cfg, proj, 3, "data/outline/chapters/01.md",
            "data/setting/empty.json", "data/summaries/rolling.md", [])
        check("任务书里是原设定集（empty.json）的绝对路径",
              str(empty.resolve()) in task3, task3[-400:])
        check("没有拼出一个空摘录 ch03_setting.md",
              not (tmp / "data" / "state" / "context" / "ch03_setting.md").exists())

        # ---- 3. 内联块与摘录同源 ----
        print("\n【3】摘录与任务书里的内联块同源（不是另写一份）")
        if ex.exists():
            text = ex.read_text(encoding="utf-8")
            check("摘录里带 locked 语义（时间线/条目）", "locked" in text or "不可违逆" in text,
                  text[:120])
    finally:
        os.chdir(old)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
