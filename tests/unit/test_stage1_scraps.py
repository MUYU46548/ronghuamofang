# -*- coding: utf-8 -*-
"""stage1 --scraps 集成自检（纯 fake，零 LLM、零网络）。

在**临时工作目录**里跑（复制 prompts/ 与 config/），避免污染真实 data/ 产物。
覆盖：
- 碎片分支开关（use_scraps / --no-scraps 等价性）
- 碎片 → 索引 → 提炼 → 作为额外输入喂给归并任务（含目录内联格式是否正确，防静默丢文件）
- 指纹：改碎片触发重归并；仅改 mtime 不触发（防每跑一次就烧钱重归并）
- 超限分批 + Python 侧合并去重编号
- _backup / 隐藏文件不进索引

用法：python tests/test_stage1_scraps.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import utils.scrap_cluster as sc              # noqa: E402
from utils.fake_client import FakeClient       # noqa: E402
from utils.progress_manager import ProgressManager  # noqa: E402
from utils.llm_client import extract_input_paths, inline_inputs  # noqa: E402
import stage1_consolidate as s1                # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


FRAG_A = "2024年3月5日 月神那点事.md"
FRAG_B = "月兔密室随手写.md"
FRAG_C = "买菜找零.txt"


def build_workspace(tag="ws"):
    """搭一个最小项目目录：prompts/ + config/ + 碎片目录；素材卡指向真实目录（只读）。"""
    tmp = Path(tempfile.mkdtemp(prefix="stage1_scraps_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts")
    (tmp / "config").mkdir()
    real_raw = (ROOT / "materials" / "raw").resolve()
    (tmp / "config" / "project.yaml").write_text(
        "book:\n"
        "  name: \"测试书\"\n"
        "  chapters: 3\n"
        "materials:\n"
        "  dir: %r\n"
        "  scraps_dir: \"materials/original_scraps\"\n"
        "  use_scraps: true\n"
        "vault:\n"
        "  readonly: true\n"
        "  vault_links: \"materials/vault_links.md\"\n"
        "run:\n"
        "  output_word: false\n" % str(real_raw), encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "materials" / "original_scraps").mkdir(parents=True)
    (tmp / "data").mkdir()
    return tmp


def add_fragments(tmp):
    d = tmp / "materials" / "original_scraps"
    (d / FRAG_A).write_text(
        "月神这个人，沉默到让人害怕。他造了月兔，却没给他们同类。\n"
        "待定：他左耳那道抓痕是谁留的？以后再想。\n", encoding="utf-8")
    (d / FRAG_B).write_text(
        "月兔密室在月兔之城下面。月神几乎不去正殿，只在书房待着。\n", encoding="utf-8")
    (d / FRAG_C).write_text("今天买菜，找零少了两块五。\n", encoding="utf-8")


def run_in(tmp, client=None, with_scraps=None):
    """在 tmp 为 cwd 的前提下跑一次 stage1。返回 (ok, msg)。"""
    os.chdir(tmp)
    cfg = {"chapter": {"target_words": [2000, 3000]}}
    import yaml
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    return s1.run_stage(cfg, proj, progress, None, None,
                        client=client or FakeClient(),
                        task_dir=str(tmp / "data" / "state" / "tasks"),
                        with_scraps=with_scraps)


def case_task_format():
    print("\n=== 1. 碎片任务的内联格式（防 llm_client 静默丢文件）===")
    tmp = build_workspace("fmt")
    try:
        add_fragments(tmp)
        os.chdir(tmp)
        index, batches, _ = s1.prepare_scraps("materials/original_scraps",
                                             "data/setting/scraps_index.json")
        check("索引生成", index is not None and index["stats"]["scrap_count"] == 3,
              index and index["stats"])
        body = s1.build_scraps_task("data/setting/scraps_index.json",
                                    "materials/original_scraps",
                                    "data/setting/scraps_merge.json")
        refs = extract_input_paths(body)
        dirs = [p for p, is_dir in refs if is_dir]
        check("目录引用被识别为目录（'下的 *.md' 格式正确）", len(dirs) == 1, refs)
        check("索引文件被识别为输入", any(p.endswith("scraps_index.json") for p, _ in refs), refs)

        inlined, missing = inline_inputs(body)
        check("碎片正文真的被内联进任务（否则模型看不到碎片）",
              "月兔密室在月兔之城下面" in inlined and not missing, missing)
        check("归并结果路径被列为期望输出（不会被当成输入吞掉）",
              any(p.endswith("scraps_merge.json") for p, _ in refs) is False)

        out_paths = s1.build_setting_task(
            {}, "data/setting/materials_manifest.json", "data/setting/normalized",
            extra_inputs=["data/setting/scraps_merge.json"])
        check("归并任务含碎片提炼结果输入行", "碎片提炼结果" in out_paths)
        refs2 = extract_input_paths(out_paths)
        check("碎片提炼结果被识别为输入",
              any(p.endswith("scraps_merge.json") for p, _ in refs2), refs2)
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_switch_and_flow():
    print("\n=== 2. 开关与完整流程（fake）===")
    tmp = build_workspace("flow")
    try:
        add_fragments(tmp)
        os.chdir(tmp)

        ok, msg = s1.run_stage(
            __import__("yaml").safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8")),
            __import__("yaml").safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8")),
            ProgressManager(str(tmp / "data" / "state" / "progress.json")),
            None, None, client=FakeClient(), task_dir=str(tmp / "data" / "state" / "tasks"),
            with_scraps=False)
        check("with_scraps=False 时仍能完成（退化为旧流程）", ok, msg)
        check("with_scraps=False 时不写碎片索引",
              not (tmp / "data" / "setting" / "scraps_index.json").exists())
        check("with_scraps=False 时不写碎片提炼产物",
              not (tmp / "data" / "setting" / "scraps_merge.json").exists())
        check("设定集已生成（旧流程未破坏）",
              (tmp / "data" / "setting" / "setting.json").exists())
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = build_workspace("flow2")
    try:
        add_fragments(tmp)
        os.chdir(tmp)
        ok, msg = run_in(tmp, with_scraps=True)
        check("with_scraps=True 完整跑通", ok, msg)
        check("碎片索引已生成",
              (tmp / "data" / "setting" / "scraps_index.json").exists())
        merge = tmp / "data" / "setting" / "scraps_merge.json"
        check("碎片提炼产物已生成", merge.exists())
        if merge.exists():
            data = json.loads(merge.read_text(encoding="utf-8"))
            check("提炼产物结构合规（points 数组）", isinstance(data.get("points"), list))
        task = tmp / "data" / "state" / "tasks" / "stage1_setting.md"
        body = task.read_text(encoding="utf-8")
        check("碎片提炼结果被喂进归并任务", "碎片提炼结果" in body)
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_fingerprint():
    print("\n=== 3. 指纹：改碎片必须触发重归并，改 mtime 不能 ===")
    tmp = build_workspace("fp")
    try:
        add_fragments(tmp)
        os.chdir(tmp)
        ok1, msg1 = run_in(tmp, with_scraps=True)
        check("首轮归并完成", ok1, msg1)

        ok2, msg2 = run_in(tmp, with_scraps=True)
        check("无变化重跑 → 复用设定集（不烧钱）", "复用设定集" in msg2, msg2)

        f = tmp / "materials" / "original_scraps" / FRAG_A
        t = f.stat().st_mtime
        os.utime(f, (t + 300, t + 300))
        ok3, msg3 = run_in(tmp, with_scraps=True)
        check("仅改 mtime → 仍复用（指纹只看内容）", "复用设定集" in msg3, msg3)

        f.write_text(f.read_text(encoding="utf-8") + "\n补一句：他其实记得真名。\n",
                     encoding="utf-8")
        ok4, msg4 = run_in(tmp, with_scraps=True)
        check("改碎片正文 → 触发重新归并（这是本次改动最关键的一条）",
              "复用设定集" not in msg4 and ok4, msg4)

        newf = tmp / "materials" / "original_scraps" / "新碎片想到一点.md"
        newf.write_text("歌唯的乐器店在正弦司隔壁。\n", encoding="utf-8")
        ok5, msg5 = run_in(tmp, with_scraps=True)
        check("新增碎片 → 触发重新归并", "复用设定集" not in msg5 and ok5, msg5)
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_batching():
    print("\n=== 4. 超限分批 + 合并去重编号 ===")
    tmp = build_workspace("batch")
    try:
        d = tmp / "materials" / "original_scraps"
        args = {"月神与月兔的设定草稿": "月神与月兔" * 300,
                "月兔密室与正殿": "月兔密室正殿" * 300,
                "歌唯的乐器店": "歌唯乐器店" * 300}
        for n, t in args.items():
            (d / (n + ".md")).write_text(t + "\n", encoding="utf-8")
        old = sc.INLINE_CHAR_BUDGET
        sc.INLINE_CHAR_BUDGET = 1500         # 压小上限，逼出多批
        try:
            os.chdir(tmp)
            index, batches, _ = s1.prepare_scraps("materials/original_scraps",
                                                 "data/setting/scraps_index.json")
        finally:
            sc.INLINE_CHAR_BUDGET = old
        check("碎片量超限 → 拆多批", len(batches) > 1, len(batches))

        os.chdir(tmp)
        ok, msg, mp = s1.run_scraps_merge(FakeClient(), str(tmp / "data" / "state" / "tasks"),
                                         "data/setting/scraps_index.json",
                                         "materials/original_scraps", batches,
                                         merge_path=str(tmp / "data" / "setting" / "scraps_merge.json"))
        check("多批提炼跑通", ok, msg)
        data = json.loads(Path(mp).read_text(encoding="utf-8"))
        ids = [p["id"] for p in data["points"]]
        check("多批结果已合并", len(ids) >= len(batches), ids)
        check("合并后 id 不重复（各批模型都从 p001 开始也不会撞号）",
              len(ids) == len(set(ids)), ids)
        check("待定事项被保留（不当作既成事实丢给写作）",
              isinstance(data.get("open_questions"), list), data.get("open_questions"))
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_ignore_noise():
    print("\n=== 5. 备份目录/隐藏文件不进碎片索引 ===")
    tmp = build_workspace("noise")
    try:
        d = tmp / "materials" / "original_scraps"
        (d / "正常碎片.md").write_text("月神造了月兔。\n", encoding="utf-8")
        (d / "_backup").mkdir()
        (d / "_backup" / "旧版.md").write_text("备份不该进索引。\n", encoding="utf-8")
        (d / ".hidden.md").write_text("隐藏不该进索引。\n", encoding="utf-8")
        os.chdir(tmp)
        index, _b, _w = s1.prepare_scraps("materials/original_scraps",
                                         "data/setting/scraps_index.json")
        names = [s["name"] for c in index["clusters"] for s in c["scraps"]]
        check("只保留正常碎片", names == ["正常碎片.md"], names)
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_empty_scraps():
    print("\n=== 6. 没有碎片时不报错（对老用户零影响）===")
    tmp = build_workspace("empty")
    try:
        os.chdir(tmp)
        index, batches, warnings = s1.prepare_scraps("materials/original_scraps",
                                                    "data/setting/scraps_index.json")
        check("空碎片目录 → 返回 None，调用方跳过碎片分支",
              index is None and batches == [], (index, batches))
        ok, msg = run_in(tmp, with_scraps=True)
        check("use_scraps=true 但无碎片 → 正常完成", ok, msg)
        check("无碎片时不生成索引文件",
              not (tmp / "data" / "setting" / "scraps_index.json").exists())
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 70)
    print("stage1 --scraps 集成自检（fake 模式，临时工作目录）")
    print("=" * 70)
    for fn in (case_task_format, case_switch_and_flow, case_fingerprint,
               case_batching, case_ignore_noise, case_empty_scraps):
        try:
            fn()
        except Exception as e:      # noqa: BLE001
            FAIL.append(fn.__name__)
            import traceback
            print("  [ERROR] %s → %s" % (fn.__name__, e))
            traceback.print_exc()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 70)
    raise SystemExit(1 if FAIL else 0)
