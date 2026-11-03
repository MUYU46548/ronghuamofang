# -*- coding: utf-8 -*-
"""扫描由 AI 介入：出厂不预设目录 + 证据包（2026-11-03 作者拍板）。

## 两条决定
1. **出厂五个默认映射清空**：`设定/人物` 这类值只对某一个人的库成立，
   留着会让用户以为"配好了"。
2. **扫描不是让用户一个个点**：`/config/vault_probe` 把证据（目录/篇数/样本文件名/
   首行）交给 AI，AI 给**提案**，作者在设置页确认。分工：AI 读证据、人做决定。

## 判据
- S1 出厂默认五键全空，且 get_vault_dirs 不再回退默认
- S2 空值不回落成 vault 根（否则会把全库当"角色目录"扫）
- S3 未指定说"不是故障"，不是"目录不存在"
- S4 probe 返回证据：目录 + 篇数 + 样本文件名 + 首行，且不含结论
- S5 接线：HTTP /config/vault_probe、MCP nf_vault_probe
用法：python tests/unit/test_vault_probe.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name); print("  [PASS] " + name)
    else:
        FAIL.append(name); print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    sch = (ROOT / "scripts" / "utils" / "setting_schema.py").read_text(encoding="utf-8")
    check("S1 出厂默认五键全空",
          all(('"%s": ""' % k) in sch for k in
              ("characters", "locations", "factions", "concepts", "timeline")))
    check("S1 get_vault_dirs 不回退默认", "out[k] = v or d" not in sch and "out[k] = str(v)" in sch)

    br = (ROOT / "scripts" / "obsidian_bridge.py").read_text(encoding="utf-8")
    check("S2 空串不回落成 vault 根", "__未指定__" in br)
    check("S3 未指定与配错分开说",
          "未指定" in br and "这一类本轮不注入（不是故障）" in br)

    probe = (ROOT / "scripts" / "nf_api_domains" / "vault_probe.py").read_text(encoding="utf-8")
    check("S4 证据包含样本文件名与首行", "samples" in probe and "heads" in probe)
    check("S4 明确只给提案不给结论", "提案" in probe and "你不能自己保存" in probe)
    check("S4 跳过集合参与扫描", "get_skip_dirs" in probe)

    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("S5 HTTP /config/vault_probe", '"/config/vault_probe"' in api)
    mcp = (ROOT / "scripts" / "nf_mcp.py").read_text(encoding="utf-8")
    check("S5 MCP nf_vault_probe", '"name": "nf_vault_probe"' in mcp)
    sk = (ROOT / "skills" / "nf-plan" / "SKILL.md").read_text(encoding="utf-8")
    check("S5 技能卡写明分工", "nf_vault_probe" in sk and "保存不是你能做的" in sk)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL)); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
