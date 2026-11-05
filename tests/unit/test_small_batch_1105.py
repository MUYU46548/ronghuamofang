# -*- coding: utf-8 -*-
"""一轮小项收口：知识库三态 / 全书目标字数 / 对话段占比上限 / 章标题入口。

对应方寸卡：task-20261104-028 / -024 / -026 / -022。

## 判据

- K1 vault 三态：未启用=静默、启用未配目录=一次、配错=一次（且三态可查）
- K2 全书目标字数：读口给三者推算；写口走 set_book_fields 且越界拒收
- K3 对话段占比上限：写作提示词里出现上限百分比；阶段 5 只标“需人工抽样”不判合格
- K4 章标题：设置页标题带“排版 / Word”字样，导出页有跳转按钮，手册写了三步到达

用法：python tests/unit/test_small_batch_1105.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 70)
    print("小项收口（-028 / -024 / -026 / -022）")
    print("=" * 70)

    bridge = (ROOT / "scripts" / "obsidian_bridge.py").read_text(encoding="utf-8")
    proj = (ROOT / "scripts" / "nf_api_domains" / "project.py").read_text(encoding="utf-8")
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    rules = (ROOT / "scripts" / "utils" / "writing_rules.py").read_text(encoding="utf-8")
    struct = (ROOT / "scripts" / "utils" / "judge" / "structure.py").read_text(encoding="utf-8")
    helpmd = (ROOT / "console" / "src" / "help" / "08-writing-rules.md").read_text(encoding="utf-8")

    print("\n[K1] 知识库扫描三态（-028）")
    check("有三态常量", 'VAULT_STATES = ("disabled", "unconfigured", "mismatch", "ok")' in bridge)
    check("同类警告只打一次", "def _warn_once(" in bridge and "_WARNED" in bridge)
    check("全没配只出一条说明", "vault-unconfigured" in bridge)
    check("“扫描结果全空”只在配了目录时说", 'if _dirs_configured:' in bridge)
    check("scan meta 带 state", '"state": state,' in bridge)
    check("预览端点带 state", '"state": "disabled" if vault is None else "unconfigured"' in bridge)
    check("界面显示未配状态", "state === 'unconfigured'" in app)

    print("\n[K2] 全书目标字数（-024）")
    check("GET/POST 端点都在", "handle_book_target_words" in proj and "handle_book_target_words_set" in proj)
    check("写盘走 set_book_fields", "set_book_fields({\"target_words\": n})" in proj)
    check("越界拒收", "3000 <= n <= 20000000" in proj)
    check("路由 GET", '"/config/book_target_words", handle_book_target_words' in proj)
    check("分发器 POST", 'elif p == "/config/book_target_words"' in api)
    check("界面有三者推算", "章 × 单章中位" in app and "按推算填入" in app)

    print("\n[K3] 对话段占比上限（-026）")
    check("默认值 0.35 写进出厂配置", "max_dialogue_ratio: 0.35" in
          (ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))
    check("有 numeric_block", "def numeric_block(" in rules and "篇幅配比硬指标" in rules)
    check("0/非法 = 不注入", "if not (0 < v <= 1):" in rules)
    check("整章写作接上", "_dialogue_ratio_block(cfg)" in
          (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8"))
    check("加强模式也接上", "_dialogue_ratio_block(cfg)" in
          (ROOT / "scripts" / "utils" / "enhanced_chapter.py").read_text(encoding="utf-8"))
    check("润色也接上", "_dialogue_ratio_block()" in
          (ROOT / "scripts" / "stage6_polish.py").read_text(encoding="utf-8"))
    check("超限不并进 needs_sampling（语义分开）",
          '"needs_sampling": bool(trig),' in struct)
    check("阶段 5 单独报告超限章", "对话段占比超过上限" in
          (ROOT / "scripts" / "stage5_check.py").read_text(encoding="utf-8"))
    check("阶段 5 明说不影响验收", "只报告，不影响验收" in
          (ROOT / "scripts" / "stage5_check.py").read_text(encoding="utf-8"))
    check("汇总里列超限章", '"over_dialogue_max"' in struct)

    print("\n[K4] 章标题入口（-022）")
    check("设置页标题带“排版 / Word”", "章标题格式（排版 / Word 相关）" in app)
    check("导出页有跳转按钮", "章标题样式在哪改" in app)
    check("手册写了三步到达", "三步到达" in helpmd and "章标题格式（排版 / Word 相关）" in helpmd)

    print("\n" + "=" * 70)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
