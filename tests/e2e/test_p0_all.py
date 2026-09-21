# -*- coding: utf-8 -*-
"""P0+P1 综合验证：断点续跑 + 安全加固 + 长程一致性 + 设定结构化编辑。"""
import io, sys, shutil, importlib, json, yaml, ast
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

print("=" * 60)
print("  P0+P1 综合验证 — 绒花墨坊")
print("=" * 60)

# ============================================================
# 任务 1: is_chapter_complete 断点续跑
# ============================================================
print("\n[任务1] 断点续跑粒度优化")
vc_mod = importlib.import_module("utils.verify_chapter")
is_chapter_complete = vc_mod.is_chapter_complete

tmp_dir = ROOT / "_tmp_p0_test"
tmp_dir.mkdir(exist_ok=True)
p = tmp_dir / "test.md"

p.write_text("", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "空文件判定未完成")

p.write_text("## 第1\n\n截断...", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "截断标记判定未完成")

p.write_text("## 第1章\n\nTODO待填", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "占位符判定未完成")

p.write_text("## 第1章\n\n" + "a"*50, encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "字数不足判定未完成")

# ⚠️ 「正常章节」夹具必须产出**合法产物**（本项目血泪纪律）。
# 这里曾是 `"内容" * 150` —— 单段 + 同一词复读 150 次，正是正文退化
# 判据要拒的形态。它靠「旧判据不看内容」一直放行，加严判据后变红。
# **修夹具，不要放宽判据。** 合法正文取自 `utils.fake_client._BODY_CORPUS`
# （94 句逐句独立撰写，句间不共享 12 字片段）。
from utils.fake_client import _BODY_CORPUS            # noqa: E402
_paras = ["".join(_BODY_CORPUS[i:i + 2]) for i in range(0, 30, 2)]
p.write_text("## 第1章 测试\n\n" + "\n\n".join(_paras), encoding="utf-8")
ok(is_chapter_complete(p, 100, 3000), "正常章节判定完成")

ok(not is_chapter_complete(tmp_dir / "nope.md", 100, 3000), "文件不存在判定未完成")

# ============================================================
# 任务 6: price_wizard eval 后门清除
# ============================================================
print("\n[任务6] 安全加固（eval 后门）")
src = (ROOT / "scripts" / "price_wizard.py").read_text(encoding="utf-8")
ok("ast.literal_eval(clean)" in src, "使用 ast.literal_eval")
ok("eval(" not in src.replace("ast.literal_eval", "").replace("eval 不处理", "").replace("简单 eval", ""), "无裸 eval() 调用")

# 安全测试
malicious = '__import__("os").system("echo pwned")'
try:
    ast.literal_eval(malicious)
    ok(False, "拒绝恶意输入")
except (ValueError, SyntaxError):
    ok(True, "ast.literal_eval 拒绝函数调用")

# ============================================================
# 任务 2: 长程一致性（滚动摘要窗口 + 角色卡锁定）
# ============================================================
print("\n[任务2] 长程一致性")

# 滚动摘要窗口
sc_mod = importlib.import_module("utils.summary_chain")
ok(sc_mod.append_chapter_summary.__defaults__[0] == 15, "summary_chain 默认窗口为 15")

# 实际测试窗口行为
rp = tmp_dir / "rolling.md"
for i in range(1, 21):
    sc_mod.append_chapter_summary(rp, i, f"第{i}章摘要", max_recent=15)
data = sc_mod.load_rolling(rp)
ok(len(data["chapters"]) == 15, "滚动摘要窗口限制为 15 章")
ok(min(data["chapters"]) == 6, "最早 5 章已被压缩")

# 角色卡提取
s4_mod = importlib.import_module("stage4_writing")
setting_dir = tmp_dir / "setting_test"
setting_dir.mkdir(exist_ok=True)
sp = setting_dir / "setting.json"
op = setting_dir / "01.md"
setting = {
    "characters": [
        {"name": "露汐", "type": "主角", "tags": ["人类", "火属性"], "snippet": "冷静寡言的少女"},
        {"name": "林远", "type": "配角", "tags": ["水属性"], "snippet": "热血冲动"},
    ]
}
sp.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")
op.write_text("## 第1章\n涉及角色：露汐、林远\n事件：...", encoding="utf-8")
cards = s4_mod._extract_character_cards_for_chapter(str(sp), str(op))
ok("露汐" in cards, "提取到露汐")
ok("林远" in cards, "提取到林远")

# 配置文件
with open(ROOT / "config/system.yaml", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
ok(cfg["chapter"]["summary_every"] == 15, "config summary_every=15")

# prompt 模板
tpl = (ROOT / "prompts" / "stage4_writing.md").read_text(encoding="utf-8")
ok("character_cards" in tpl, "stage4 prompt 模板包含 {{character_cards}}")

# ============================================================
# 任务 3: 设定页签结构化编辑 (GUI)
# ============================================================
print("\n[任务3] 设定页签结构化编辑")
vue_src = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
ok("settingEditor.key === 'characters'" in vue_src, "角色编辑表单有专用模板")
ok("settingEditor.key === 'world'" in vue_src, "世界观编辑表单有专用模板")
ok("locked" in vue_src, "支持 locked 标记")
ok("_tagsInput" in vue_src, "标签输入框支持逗号分隔")
ok("concepts" in vue_src, "支持概念/子类目的编辑")

# ============================================================
# 清理
# ============================================================
shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n" + "=" * 60)
print(f"  结果: {PASS} pass / {FAIL} fail")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
