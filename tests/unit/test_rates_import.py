# -*- coding: utf-8 -*-
"""定价批量导入自检（2026-09-29）。

背景：定价面板此前只能**逐行手工编辑**（09-23 上线的 /costs/rates）。要把账单页 /
文档里的整张价格表搬进来得一行行敲；用户想直接粘贴 JSON 或从源码 RATES 块复制，
更是完全没法用。本次新增 `POST /costs/rates/import`（两段式：预览 → 确认）。

本自检覆盖：
A. 解析：表格行（空白/制表/逗号/带单位/千分位/免费）、JSON 各形状、Python 字面量。
B. 留痕：识别不了的行必须进 warnings、语义错误必须进 errors —— **不许静默丢条目**。
C. 合并策略：cache_read 缺省时**沿用现有值**，而不是悄悄置 0
   （cache 单价低于 in 价，置 0 等于把命中部分当不花钱，账单会偏乐观）。
D. 落盘：confirm=false **一个字节都不写**（反证）；upsert 只覆盖同名、不删其他；落盘前留 .bak。
E. 有解析错误时 confirm=true → 400 且**文件不变**。
F. 回归：正常自定义定价读写不受影响。

全程离线、零网络、零费用。
用法：python tests/unit/test_rates_import.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import cost_tracker                                   # noqa: E402
from nf_api_domains import misc as dom_misc                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


class Sandbox:
    """把自定义定价落到临时文件，绝不碰仓库里的 data/state/cost_rates.json。"""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nf_rates_"))
        self.path = self.dir / "cost_rates.json"
        self._orig = cost_tracker.CUSTOM_RATES_PATH

    def __enter__(self):
        cost_tracker.CUSTOM_RATES_PATH = self.path
        return self

    def __exit__(self, *exc):
        cost_tracker.CUSTOM_RATES_PATH = self._orig
        return False

    def write(self, rates):
        cost_tracker.save_custom_rates(rates)

    def raw(self):
        return self.path.read_text(encoding="utf-8") if self.path.exists() else None


# ---------------------------------------------------------------- A. 解析
def test_parse_table():
    print("\n[A] 表格行：各种分隔符与写法")
    cases = [
        ("空格三列", "kimi-k2.6  1.0  4.2  0.2",
         {"cache_read": 0.2}),
        ("制表符两列", "kimi-k2.6\t1.0\t4.2", {}),
        ("单位与货币符", "kimi-k2.6  ¥1.00/M  ￥4.2元/百万tokens", {}),
        ("带空格单位", "kimi-k2.6\t¥1.00 / M tokens\t¥4.20 / M tokens\t¥0.20 / M tokens",
         {"cache_read": 0.2}),
        ("中文单位", "glm-5.3  8.0 元 / 百万 tokens  28.0 元 / 百万 tokens  2.0 元 / 百万 tokens",
         {"cache_read": 2.0}),
        ("逗号分列", "kimi-k2.6,1.0,4.2,0.2", {"cache_read": 0.2}),
        ("竖线与分号", "a|1.0|2.0\nb;3.0;4.0", None),
        ("千分位", "big 1,000.50 4,200.25", {}),
        ("免费", "hunyuan-lite 免费 免费", {}),
    ]
    for label, txt, expect_extra in cases:
        parsed, errors, warnings, fmt = cost_tracker.parse_rates_text(txt)
        ok = not errors and parsed and fmt == "table"
        if ok and expect_extra is not None:
            row = next(iter(parsed.values()))
            ok = all(row.get(k) == v for k, v in expect_extra.items())
        check("表格：" + label, bool(ok), "parsed=%s errors=%s" % (parsed, errors))

    parsed, _, _, _ = cost_tracker.parse_rates_text("big 1,000.50 4,200.25")
    check("千分位不被当成分隔符（1000.5 而非 1）",
          parsed.get("big", {}).get("in") == 1000.5, parsed)

    parsed, _, _, _ = cost_tracker.parse_rates_text("DeepSeek V4.1 Flash 1.0 4.2")
    check("模型名带空格且含版本号（V4.1 归名字不归价格）",
          parsed.get("DeepSeek V4.1 Flash", {}).get("out") == 4.2, parsed)


def test_parse_json_and_literal():
    print("\n[A] JSON / Python 字面量")
    cases = [
        ("JSON dict-of-dict", '{"kimi-k2.6": {"in": 1, "out": 4.2, "cache_read": 0.2}}'),
        ("JSON list-of-dict", '[{"model": "a", "in": 1, "out": 2}, {"name": "b", "in": 3, "out": 4}]'),
        ("JSON 数组值", '{"kimi-k2.6": [1, 4.2, 0.2]}'),
        ("JSON rates 包一层", '{"rates": {"a": {"in": 1, "out": 2}}}'),
        ("JSON 中文键单条", '{"模型名": "kimi-k2.6", "输入": 1, "输出": 4.2}'),
        ("Python 字面量（RATES 复制）",
         '    "deepseek-v4-pro":    {"in": 12.0, "out": 24.0, "cache_read": 1.0},\n'
         '    "kimi-k2.6": {"in": 1.0, "out": 4.2},\n'),
    ]
    for label, txt in cases:
        parsed, errors, warnings, fmt = cost_tracker.parse_rates_text(txt)
        check("解析：" + label, bool(parsed) and not errors,
              "parsed=%s errors=%s" % (parsed, errors))

    parsed, _, _, fmt = cost_tracker.parse_rates_text(
        '"deepseek-v4-pro": {"in": 12.0, "out": 24.0},\n"kimi-k2.6": {"in": 1.0, "out": 4.2},')
    check("Python 字面量：尾逗号/缩进可容忍，两条都进", len(parsed) == 2 and fmt == "python", parsed)

    parsed, _, _, fmt = cost_tracker.parse_rates_text('{"model": "x", "in": 1, "out": 2}')
    check("JSON 单条（顶层带 model 键）不误判成 {模型: spec}", parsed.get("x", {}).get("in") == 1.0, parsed)


# ---------------------------------------------------------------- B. 留痕
def test_no_silent_drop():
    print("\n[B] 识别不了 / 错的输入必须留痕（不许静默丢）")
    txt = "模型 输入 输出 缓存\n\n# 备忘：以上含税\nkimi-k2.6 1.0 4.2 0.2\n// 旧价\n"
    parsed, errors, warnings, _ = cost_tracker.parse_rates_text(txt)
    check("表头行被跳过但留在 warnings", not errors and len(parsed) == 1
          and any("表头" in w or "跳过" in w for w in warnings),
          "warnings=%s" % warnings)
    check("注释行不算错误（用户显式标注）", not any("备忘" in e for e in errors), errors)

    cases = [
        ("只有 1 个数值", "kimi-k2.6 1.0", "只解析出 1 个数值"),
        ("缺模型名", "1.0 4.2", "缺少模型名"),
        ("负数", "kimi-k2.6 -1 4.2", "负数"),
        ("非数值", "kimi-k2.6 abc def", "跳过"),
    ]
    for label, one, needle in cases:
        _, errors, warnings, _ = cost_tracker.parse_rates_text(one)
        hit = any(needle in e for e in errors) or any(needle in w for w in warnings)
        check("坏输入有明确提示：" + label, hit, "errors=%s warnings=%s" % (errors, warnings))

    _, errors, _, _ = cost_tracker.parse_rates_text("kimi-k2.6 1.0")
    check("错误信息带行号（便于定位）", any(e.startswith("第 1 行") for e in errors), errors)

    parsed, errors, warnings, _ = cost_tracker.parse_rates_text("kimi 1 2\nkimi 3 4\n")
    check("同一次粘贴内重复 → warning 且取后者",
          parsed.get("kimi", {}).get("in") == 3.0 and any("重复" in w for w in warnings),
          "parsed=%s warnings=%s" % (parsed, warnings))

    parsed, errors, _, fmt = cost_tracker.parse_rates_text("计费说明：以上均为含税价\n")
    check("纯说明文本 → 报错而非静默成功（fmt=%s）" % fmt, not parsed and bool(errors), errors)

    _, errors, _, _ = cost_tracker.parse_rates_text("")
    check("空输入 → 明确报错", bool(errors), errors)


# ---------------------------------------------------------------- C. 合并策略
def test_merge_policy():
    print("\n[C] 合并策略：cache_read 缺省沿用现有值（不许悄悄置 0）")
    with Sandbox() as sb:
        # 现有自定义：kimi-k2.6 带缓存价
        sb.write({"kimi-k2.6": {"in": 9.9, "out": 9.9, "cache_read": 0.2}})
        raw_before = sb.raw()
        rep = cost_tracker.plan_rates_import("kimi-k2.6 1.0 4.2\n")
        row = rep["plan"][0]
        check("未给缓存价 → 沿用现有 0.2（**不是 0**）",
              row["to"].get("cache_read") == 0.2, row)
        check("沿用行为有 warning 说明", any("沿用" in w for w in rep["warnings"]), rep["warnings"])
        check("in/out 用新值覆盖", row["to"]["in"] == 1.0 and row["to"]["out"] == 4.2, row)
        check("动作标记为 update（已有自定义）", row["action"] == "update", row)

        # 全新模型：现有条目也没有缓存价 → 置 0 + warning
        rep = cost_tracker.plan_rates_import("brand-new-model 1.0 4.2\n")
        row = rep["plan"][0]
        check("全新模型：cache 置 0 且明确告警",
              row["to"]["cache_read"] == 0.0 and any("按 0 计" in w for w in rep["warnings"]), row)
        check("动作标记为 new", row["action"] == "new", row)

        # 命中源码刊例价 → override
        rep = cost_tracker.plan_rates_import("glm-5.3 99 99 9\n")
        row = rep["plan"][0]
        check("命中源码 RATES → 动作 override 且带出原值",
              row["action"] == "override" and row["from"] and row["from"]["in"] == 8.0, row)

        check("预览不改文件（plan 阶段零写入，逐字比对）", sb.raw() == raw_before, sb.raw())


# ---------------------------------------------------------------- D/E. 落盘与拒绝
def test_apply_and_refuse():
    print("\n[D/E] 落盘：两段式 + upsert 语义 + 有错拒写")
    with Sandbox() as sb:
        sb.write({"keep-me": {"in": 1.0, "out": 2.0, "cache_read": 0.0}})
        before = sb.raw()

        # confirm=false：一个字节都不写（反证）
        status, body = dom_misc.handle_costs_rates_import(
            None, {"text": "kimi-k2.6 1 4.2 0.2\n", "confirm": False})
        check("预览返回 200 且 applied=false", status == 200 and body["applied"] is False, status)
        check("预览期文件逐字未变（反证）", sb.raw() == before, sb.raw())

        # confirm=true：写入
        status, body = dom_misc.handle_costs_rates_import(
            None, {"text": "kimi-k2.6 1 4.2 0.2\n", "confirm": True})
        saved = json.loads(sb.raw())["rates"]
        check("确认后返回 200 且 applied=true", status == 200 and body["applied"] is True, status)
        check("写入成功：新增条目落盘", saved.get("kimi-k2.6", {}).get("out") == 4.2, saved)
        check("upsert 不删其他条目（keep-me 仍在）", "keep-me" in saved, saved)
        check("落盘前留备份 .bak", bool(body.get("backup")) and
              Path(body["backup"]).exists(), body.get("backup"))
        check("备份内容 = 导入前原文",
              Path(body["backup"]).read_text(encoding="utf-8") == before, None)

        # 有解析错误 → 拒绝写入（400），文件不变
        snap = sb.raw()
        status, body = dom_misc.handle_costs_rates_import(
            None, {"text": "kimi-k2.6 1 4.2\n坏行只有1个数 5\n", "confirm": True})
        check("有解析错误时 confirm=true → 400", status == 400, status)
        check("拒绝写入：文件逐字未变（反证）", sb.raw() == snap, sb.raw())
        check("拒绝理由回给前端（errors 非空）", bool(body.get("errors")), body)
        check("拒绝时 applied 仍为 false", body.get("applied") is False, body)

        # 再次确认：错误版本不污染
        status, body = dom_misc.handle_costs_rates_import(
            None, {"text": "kimi-k2.6 2 5 0.3\n", "confirm": True})
        saved = json.loads(sb.raw())["rates"]
        check("修正后可正常导入（覆盖同名）",
              status == 200 and saved["kimi-k2.6"]["in"] == 2.0, saved)


# ---------------------------------------------------------------- F. 回归
def test_regression():
    print("\n[F] 回归：既有自定义定价读写与源码刊例价不受影响")
    with Sandbox() as sb:
        sb.write({"a": {"in": 1.0, "out": 2.0}})
        check("load_custom_rates 正常读回", cost_tracker.load_custom_rates().get("a"), sb.raw())
        merged = cost_tracker.get_merged_rates()
        check("合并视图仍含源码刊例价（glm-5.3）", merged.get("glm-5.3", {}).get("in") == 8.0, None)
        check("自定义覆盖源码（a 不在源码里，值一致）", merged.get("a", {}).get("out") == 2.0, None)
    check("源码 RATES 的 kimi-k2.6 条目未被测试改动",
          cost_tracker.RATES.get("kimi-k2.6", {}).get("out") == 4.2, cost_tracker.RATES.get("kimi-k2.6"))


def main():
    print("=" * 62)
    print("  定价批量导入自检（离线）")
    print("=" * 62)
    test_parse_table()
    test_parse_json_and_literal()
    test_no_silent_drop()
    test_merge_policy()
    test_apply_and_refuse()
    test_regression()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
