# -*- coding: utf-8 -*-
"""任务2 验证：长程一致性（滚动摘要窗口 + 角色卡锁定）。"""
import io, sys, shutil, importlib
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = 0, 0
def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  [PASS] " + msg)
    else:
        FAIL += 1; print("  [FAIL] " + msg)

# === 测试 1: 滚动摘要窗口扩大 (summary_every: 15) ===
print("\n=== 测试1: 滚动摘要窗口扩大 ===")
vc_mod = importlib.import_module("utils.verify_chapter")
sc_mod = importlib.import_module("utils.summary_chain")

tmp_dir = ROOT / "_tmp_summary"
tmp_dir.mkdir(exist_ok=True)
rolling_path = tmp_dir / "rolling.md"

# 添加 20 章摘要，窗口为 15
for i in range(1, 21):
    sc_mod.append_chapter_summary(rolling_path, i, f"第{i}章摘要：这是一个测试摘要", max_recent=15)

# 检查最终 rolling.md 内容
data = sc_mod.load_rolling(rolling_path)
print(f"  当前窗口章数: {len(data['chapters'])}")
print(f"  保留章节号: {sorted(data['chapters'].keys())}")
ok(len(data['chapters']) == 15, "窗口限制为 15 章")
ok(min(data['chapters'].keys()) == 6, "最早章（1-5）已被压缩")
ok(20 in data['chapters'], "最新章保留")

# === 测试 2: 角色卡提取 ===
print("\n=== 测试2: 角色卡提取 ===")
s4_mod = importlib.import_module("stage4_writing")

setting_dir = tmp_dir / "setting"
setting_dir.mkdir(exist_ok=True)
setting_path = setting_dir / "setting.json"
outline_path = tmp_dir / "01.md"

# 创建测试 setting.json
import json
setting = {
    "characters": [
        {"name": "露汐", "性格": "冷静、寡言", "关系": "主角的妹妹", "禁止行为": "不可描写为热情开朗"},
        {"name": "林远", "性格": "热血、冲动", "关系": "露汐的师兄", "禁止行为": "不可描写为冷静理性"},
        {"name": "苏白", "性格": "温和、睿智", "关系": "师父", "禁止行为": ""}
    ]
}
setting_path.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")

# 创建测试大纲
outline_text = "## 第1章 开篇\n涉及角色：露汐、林远\n核心事件：..."
outline_path.write_text(outline_text, encoding="utf-8")

cards = s4_mod._extract_character_cards_for_chapter(str(setting_path), str(outline_path))
print(f"  提取的角色卡:\n{cards[:200]}…")
ok("露汐" in cards, "提取到露汐的角色卡")
ok("林远" in cards, "提取到林远的角色卡")
ok("苏白" not in cards, "未涉及的苏白不出现")

# === 测试 3: 配置文件 summary_every 已改为 15 ===
print("\n=== 测试3: 配置文件验证 ===")
import yaml
with open(ROOT / "config/system.yaml", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
ok(cfg.get("chapter", {}).get("summary_every") == 15, "config/system.yaml 中 summary_every 为 15")

# 清理
shutil.rmtree(tmp_dir, ignore_errors=True)

print(f"\n=== 结果: {PASS} pass / {FAIL} fail ===")
sys.exit(0 if FAIL == 0 else 1)
