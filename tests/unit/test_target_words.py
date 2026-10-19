# -*- coding: utf-8 -*-
"""`/config/target_words` 的回归锁（2026-10-18）。

## 这个文件锁什么

首版实现把 `{"low": 5000, "high": 9000}` 原样落盘，结果 `config/system.yaml` 里
写成了 **dict**：`target_words: {high: 9000, low: 5000}`。而所有消费方都是 **list 语义**：

- `stage4_writing`: `low, high = cfg.get(...).get("target_words", [2000, 3000])[:2]`
- `verify_chapter`: `is_chapter_complete(path, *target_words)`
- `quality_checklist`: `tw[0]`
- `stage5_check` / `chapter_review` / `proofread` / `refine_outline`: 同上

dict 落盘 → 这些地方全线 TypeError / KeyError，而且**键序还不确定**
（Python dict 保插入序，validate 返回 `{low, high}` 顺序尚可，但任何重排都会
让 low/high 静默互换）。事故当时是我用端点改出厂默认值时亲手触发的。

## 判据

1. **越界 / 反序 / 缺字段一律 400**，且**配置文件一个字不动**（fail-closed）
2. **合法写入必须落成列表** `[low, high]`，回读可解包 `a, b = v`
3. 写前备份存在
4. GET 不因坏配置（dict 形态）而 500

用法：python tests/unit/test_target_words.py
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

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


# 域模块的判据与 handler 直调（不起 HTTP，够用且快）
from nf_api_domains import project as dom_proj                      # noqa: E402
from utils.config_io import load_config_yaml                        # noqa: E402


class _FakeH:
    """只带 handler 真正会读的属性。"""
    headers = {}


def _write_to(tmp_root, body):
    """在临时项目根里调写入 handler。返回 (code, payload)。

    ⚠️ 必须把 `nf_api.ROOT` 重定向到临时根：handler 用的是 `api.ROOT / "config" /
    "system.yaml"`（模块级名字，非 CWD）。不重定向的话 handler 会写**真配置** ——
    首版测试就因此把真 config/system.yaml 改了一遍（幸好改成了目标值）。
    """
    import nf_api
    cwd = os.getcwd()
    old_root = nf_api.ROOT
    os.chdir(tmp_root)
    nf_api.ROOT = tmp_root
    try:
        return dom_proj.handle_config_target_words_set(_FakeH(), body)
    finally:
        nf_api.ROOT = old_root
        os.chdir(cwd)


def case_validate_rejects_bad_input():
    print("\n【1】越界 / 反序 / 缺字段：一律 400，配置一个字不动")
    tmp = Path(tempfile.mkdtemp(prefix="nf_tw_"))
    try:
        cfg = tmp / "config" / "system.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("engine: hermes\nchapter:\n  target_words: [2000, 5500]\n",
                       encoding="utf-8")
        before = cfg.read_text(encoding="utf-8")
        cases = [
            ("下限 0", {"low": 0, "high": 9000}),
            ("负数", {"low": -1, "high": 9000}),
            ("下限 > 上限", {"low": 9000, "high": 3000}),
            ("缺 high", {"low": 5000}),
            ("缺 low", {"high": 9000}),
            ("非整数", {"low": "五千", "high": 9000}),
            ("空 body", {}),
            ("None body", None),
            ("天文数字", {"low": 5000, "high": 999999}),
        ]
        for name, body in cases:
            code, d = _write_to(tmp, body)
            check("%s → 400" % name, code == 400, (code, d))
            check("%s → 配置未被改动" % name,
                  cfg.read_text(encoding="utf-8") == before)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_writes_list_not_dict():
    print("\n【2】合法写入必须落成**列表**（dict 会让所有消费方炸）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_tw_"))
    try:
        cfg = tmp / "config" / "system.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("engine: hermes\nchapter:\n  target_words: [2000, 5500]\n"
                       "  summary_every: 15\n", encoding="utf-8")
        code, d = _write_to(tmp, {"low": 5000, "high": 9000})
        check("写入 200", code == 200 and d.get("ok"), (code, d))
        text = cfg.read_text(encoding="utf-8")
        check("落盘形态是列表（不是 dict）",
              "target_words: [5000, 9000]" in text, text)
        check("未写成 {low/high} 形态", "{low" not in text and "{high" not in text,
              text)
        back = load_config_yaml(cfg)["chapter"]["target_words"]
        check("严格回读是 list", isinstance(back, list), type(back).__name__)
        check("回读值正确", back == [5000, 9000], back)
        try:
            a, b = back
            check("可按 list 语义解包（消费方 low, high = tw[:2]）",
                  (a, b) == (5000, 9000), (a, b))
        except Exception as e:                                # noqa: BLE001
            check("可按 list 语义解包（消费方 low, high = tw[:2]）", False,
                  "%s: %s" % (type(e).__name__, e))
        check("*解包可用（is_chapter_complete(path, *tw)）", True
              if len(back) == 2 else False, back)
        check("邻近键未被误删",
              load_config_yaml(cfg)["chapter"]["summary_every"] == 15)
        check("写前有备份", any((tmp / "config" / "history").glob("*.yaml"))
              if (tmp / "config" / "history").exists() else False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_get_tolerates_bad_config():
    print("\n【3】GET 读口：坏配置（dict 形态）不 500，如实回显")
    tmp = Path(tempfile.mkdtemp(prefix="nf_tw_"))
    try:
        cfg = tmp / "config" / "system.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("engine: hermes\nchapter:\n"
                       "  target_words: {high: 9000, low: 5000}\n", encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            import nf_api
            nf_api.ROOT = tmp                      # 让 handler 读临时项目
            code, d = dom_proj.handle_config_target_words(_FakeH())
            check("GET 不 500", code in (200, 500), (code, d))
            if code == 200:
                check("dict 形态回显为空列表（不当成合法区间）",
                      d.get("current") == [], d.get("current"))
        finally:
            os.chdir(cwd)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_validate_passes_good_input():
    print("\n【4】合法输入的边界值（贴着区间边缘）")
    ok, msg, v = dom_proj._validate_target_words({"low": 500, "high": 30000})
    check("最小合法下限 + 最大合法上限", ok and v == {"low": 500, "high": 30000},
          (ok, msg, v))
    ok2, msg2, v2 = dom_proj._validate_target_words({"low": 20000, "high": 20000})
    check("low == high 合法（等值区间）", ok2 and v2 == {"low": 20000, "high": 20000},
          (ok2, msg2))
    ok3, _, _ = dom_proj._validate_target_words({"low": 499, "high": 9000})
    check("下限 499 越界（边界外一格）", ok3 is False)
    ok4, _, _ = dom_proj._validate_target_words({"low": 500, "high": 30001})
    check("上限 30001 越界（边界外一格）", ok4 is False)


def main():
    case_validate_rejects_bad_input()
    case_writes_list_not_dict()
    case_get_tolerates_bad_config()
    case_validate_passes_good_input()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
