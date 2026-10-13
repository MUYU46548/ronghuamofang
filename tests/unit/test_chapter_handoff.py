# -*- coding: utf-8 -*-
"""丙1（handoff 接手索引）+ 丙2（三级读取协议）回归 —— 16 断言。

用法：python tests/unit/test_chapter_handoff.py
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

from chapter_handoff import (  # noqa: E402
    build_index, list_l1, get_l2, get_l3_full, validate_bindings, load_index,
    STAGE_NAMES,
)

PASS, FAIL = [], []
TMP = None
ORIG_CWD = None


def setup():
    global TMP, ORIG_CWD
    ORIG_CWD = os.getcwd()
    TMP = Path(tempfile.mkdtemp(prefix="handoff_test_"))
    os.chdir(TMP)
    data = TMP / "data"
    (data / "outline" / "chapters").mkdir(parents=True)
    (data / "chapters" / "raw").mkdir(parents=True)
    (data / "chapters" / "checked").mkdir(parents=True)
    (data / "state").mkdir(parents=True)
    (TMP / "config").mkdir(parents=True)
    # 造 3 个章节大纲
    for n in (1, 2, 3):
        title = f"第{n}章 测试"
        (data / "outline" / "chapters" / f"{n:02d}.md").write_text(
            f"# {title}\n\n- 核心事件：\n  1. 事件一\n", encoding="utf-8")
        (data / "chapters" / "raw" / f"{n:02d}.md").write_text(
            f"第{n}章正文内容", encoding="utf-8")
    # progress.json 标记 stage4 全 done
    progress = {
        "stages": {
            "4": {"status": "done", "completed_chapters": [1, 2]},
            "5": {"status": "done", "completed_chapters": [1]},
        }
    }
    (data / "state" / "progress.json").write_text(
        json.dumps(progress), encoding="utf-8")
    # project.yaml
    (TMP / "config" / "project.yaml").write_text(
        "book:\n  name: 测试之书\n", encoding="utf-8")
    return data


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:180]) if detail else ""))


def test_build():
    print("\n[1] 构建索引")
    setup()
    try:
        idx = build_index(root=TMP)
        check("1.1 返回 3 章", idx["work_identity"]["total_chapters"] == 3, idx["work_identity"])
        check("1.2 书名正确", idx["work_identity"]["book_name"] == "测试之书", idx["work_identity"]["book_name"])
        check("1.3 第一章 stage_status=done（progress 标 done）",
              idx["chapters"]["01"]["stage_status"] == "done", idx["chapters"]["01"]["stage_status"])
        check("1.4 第三章 stage_status=pending（未在 completed_chapters）",
              idx["chapters"]["03"]["stage_status"] == "pending", idx["chapters"]["03"]["stage_status"])
        check("1.5 索引文件已写盘", (TMP / "data" / "state" / "chapter_handoff.json").exists())
        check("1.6 raw_exists 标记在场", idx["chapters"]["01"].get("raw_exists") is True, idx["chapters"]["01"])
    finally:
        os.chdir(ORIG_CWD)
        shutil.rmtree(TMP, ignore_errors=True)


def test_l1_no_content():
    print("\n[2] L1 不带正文")
    setup()
    try:
        build_index(root=TMP)
        l1 = list_l1(root=TMP)
        check("2.1 L1 返 3 章", l1["total"] == 3, l1["total"])
        has_content = any("content" in c for c in l1["chapters"])
        check("2.2 L1 无 content 字段（三级协议 L1 纪律）", not has_content, has_content)
    finally:
        os.chdir(ORIG_CWD)
        shutil.rmtree(TMP, ignore_errors=True)


def test_l2_single():
    print("\n[3] L2 单章正文")
    setup()
    try:
        build_index(root=TMP)
        l2 = get_l2(1, root=TMP)
        check("3.1 读到正文", l2 is not None and l2["content"], l2)
        check("3.2 章号正确", l2["n"] == 1, l2["n"])
        check("3.3 标题在场", l2["title"], l2["title"])
        check("3.4 sha256 长度=64", len(l2["sha256"]) == 64, l2["sha256"])
        check("3.5 不存在章节 → None", get_l2(99, root=TMP) is None)
    finally:
        os.chdir(ORIG_CWD)
        shutil.rmtree(TMP, ignore_errors=True)


def test_l3_full():
    print("\n[4] L3 批量全文")
    setup()
    try:
        build_index(root=TMP)
        # 不 confirm → 返 error
        l3_no = get_l3_full(root=TMP, confirm=False)
        check("4.1 不 confirm → error", "error" in l3_no, l3_no)
        # confirm=True → 批量
        l3 = get_l3_full(root=TMP, confirm=True)
        check("4.2 confirm=True 返 3 章", l3["total"] == 3, l3["total"])
        check("4.3 每章都有 content", all("content" in c for c in l3["chapters"]), l3["chapters"][0].keys() if l3["chapters"] else None)
    finally:
        os.chdir(ORIG_CWD)
        shutil.rmtree(TMP, ignore_errors=True)


def test_validate_bindings():
    print("\n[5] 校验绑定三字段")
    setup()
    try:
        build_index(root=TMP)
        result = validate_bindings(root=TMP)
        check("5.1 全部 PASS", result["all_ok"], result["total"])
        check("5.2 3 条校验结果", result["total"] == 3, result["total"])
        # 篡改一个 sha → 应 FAIL
        bad_idx = load_index(root=TMP)
        bad_idx["chapters"]["01"]["sha256"] = "bad"
        bad_path = TMP / "data" / "state" / "bad_handoff.json"
        bad_path.write_text(json.dumps(bad_idx, ensure_ascii=False), encoding="utf-8")
        bad_result = validate_bindings(str(bad_path))
        check("5.3 sha 错误 → FAIL", not bad_result["all_ok"], bad_result["all_ok"])
    finally:
        os.chdir(ORIG_CWD)
        shutil.rmtree(TMP, ignore_errors=True)


def test_corrupt_recovery():
    print("\n[6] 损坏恢复")
    setup()
    try:
        bad_path = str(TMP / "bad.json")
        Path(bad_path).write_text("{broken", encoding="utf-8")
        check("6.1 损坏 → 空 dict", load_index(bad_path) == {}, None)
    finally:
        os.chdir(ORIG_CWD)
        shutil.rmtree(TMP, ignore_errors=True)


def main():
    test_build()
    test_l1_no_content()
    test_l2_single()
    test_l3_full()
    test_validate_bindings()
    test_corrupt_recovery()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
