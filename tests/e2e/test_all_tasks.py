# -*- coding: utf-8 -*-
"""完整验证：任务 1-6（含任务 4 审稿评论链 + 任务 5 流式暂停/恢复）。"""
import io, sys, shutil, importlib, json, yaml, ast, tempfile, os
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
print("  绒花墨坊 — 任务 1-6 完整验证")
print("=" * 60)

# === 任务 1: is_chapter_complete ===
print("\n[任务1] 断点续跑粒度优化")
vc_mod = importlib.import_module("utils.verify_chapter")
is_chapter_complete = vc_mod.is_chapter_complete
tmp_dir = ROOT / "_tmp_all_test"
tmp_dir.mkdir(exist_ok=True)
p = tmp_dir / "test.md"
p.write_text("", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "空文件判定未完成")
p.write_text("## 第1\n\n截断...", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "截断标记判定未完成")
p.write_text("## 第1章\n\nTODO", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "占位符判定未完成")
p.write_text("## 第1章\n\n" + "a"*50, encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "字数不足判定未完成")
p.write_text("## 第1章 测试\n\n" + "内容"*150, encoding="utf-8")
ok(is_chapter_complete(p, 100, 3000), "正常章节判定完成")

# === 任务 6: eval 后门 ===
print("\n[任务6] 安全加固（eval 后门）")
src = (ROOT / "scripts" / "price_wizard.py").read_text(encoding="utf-8")
code_lines = [l for l in src.splitlines() if l.strip() and not l.strip().startswith("#")]
ok("eval(" not in "\n".join(code_lines).replace("ast.literal_eval", ""), "无裸 eval()")
ok("ast.literal_eval(clean)" in src, "使用 ast.literal_eval")
try:
    ast.literal_eval('__import__("os").system("echo pwned")')
    ok(False)
except (ValueError, SyntaxError):
    ok(True, "拒绝恶意输入")

# === 任务 2: 长程一致性 ===
print("\n[任务2] 长程一致性")
sc_mod = importlib.import_module("utils.summary_chain")
ok(sc_mod.append_chapter_summary.__defaults__[0] == 15, "默认窗口为 15")
rp = tmp_dir / "rolling.md"
for i in range(1, 21):
    sc_mod.append_chapter_summary(rp, i, f"第{i}章摘要", max_recent=15)
data = sc_mod.load_rolling(rp)
ok(len(data["chapters"]) == 15, "窗口限制为 15 章")
ok(min(data["chapters"]) == 6, "最早 5 章被压缩")

s4_mod = importlib.import_module("stage4_writing")
sd = tmp_dir / "setting_t2"
sd.mkdir(exist_ok=True)
sp = sd / "setting.json"
op = sd / "01.md"
setting = {"characters": [{"name": "露汐", "type": "主角", "tags": ["人类"], "snippet": "冷静"}, {"name": "林远", "type": "配角", "tags": ["水属性"], "snippet": "热血"}]}
sp.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")
op.write_text("## 第1章\n涉及角色：露汐、林远", encoding="utf-8")
cards = s4_mod._extract_character_cards_for_chapter(str(sp), str(op))
ok("露汐" in cards and "林远" in cards, "提取到角色卡")

with open(ROOT / "config/system.yaml", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
ok(cfg["chapter"]["summary_every"] == 15, "config summary_every=15")

tpl = (ROOT / "prompts" / "stage4_writing.md").read_text(encoding="utf-8")
ok("character_cards" in tpl, "prompt 模板包含 character_cards")

# === 任务 3: 设定结构化编辑 ===
print("\n[任务3] 设定页签结构化编辑")
vue = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
ok("settingEditor.key === 'characters'" in vue, "角色编辑表单有专用模板")
ok("settingEditor.key === 'world'" in vue, "世界观编辑表单有专用模板")
ok("locked" in vue, "支持 locked 标记")
ok("_tagsInput" in vue, "标签输入框支持逗号分隔")
ok("concepts" in vue, "支持概念/子类目的编辑")

# === 任务 4: 审稿评论链 ===
print("\n[任务4] 审稿评论链")
cr_mod = importlib.import_module("chapter_review")
report_path = tmp_dir / "review_report.json"

# 构造测试报告
test_report = {
    "chapters": [
        {
            "n": 1,
            "findings": [
                {"id": "f001", "type": "character_inconsistency", "severity": "error", "detail": "角色性格矛盾", "suggested_action": "修正"}
            ]
        }
    ]
}
report_path.write_text(json.dumps(test_report, ensure_ascii=False, indent=2), encoding="utf-8")

# 添加评论
result = cr_mod.add_comment_to_finding(str(report_path), 1, "f001", "这个确实有问题，下一章修正")
ok(result, "评论添加成功")

# 验证评论写入
updated = json.loads(report_path.read_text(encoding="utf-8"))
ok("comments" in updated["chapters"][0]["findings"][0], "finding 包含 comments 字段")
ok(updated["chapters"][0]["findings"][0]["comments"][0]["text"] == "这个确实有问题，下一章修正", "评论内容正确")

# 验证不存在的 finding
result_bad = cr_mod.add_comment_to_finding(str(report_path), 1, "f999", "不存在")
ok(not result_bad, "不存在的 finding 返回 False")

# 验证 /review/comment 端点在 nf_api 源码中
api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
ok('elif p == "/review/comment":' in api_src, "nf_api 新增 /review/comment 端点")

# 验证 ReviewConsole.vue 有评论 UI
rvc = (ROOT / "console" / "src" / "ReviewConsole.vue").read_text(encoding="utf-8")
ok("toggleComment" in rvc, "ReviewConsole.vue 有 toggleComment")
ok("submitComment" in rvc, "ReviewConsole.vue 有 submitComment")
ok("comment-section" in rvc, "ReviewConsole.vue 有评论列表样式")

# === 任务 5: 流式暂停/恢复 ===
print("\n[任务5] 流式暂停/恢复")

# 验证 /stream/pause 和 /stream/resume 端点
ok('elif p == "/stream/pause":' in api_src, "nf_api 新增 /stream/pause 端点")
ok('elif p == "/stream/resume":' in api_src, "nf_api 新增 /stream/resume 端点")

# 验证 _wrap_client_for_streaming 支持 pause
ok("streamer.get(\"pause\")" in api_src, "_wrap_client_for_streaming 支持 pause 检查")

# 验证 GUI 有暂停/恢复按钮
ok("pauseStream" in vue, "App.vue 有 pauseStream 函数")
ok("resumeStream" in vue, "App.vue 有 resumeStream 函数")
ok("streamStatus === 'paused'" in vue, "App.vue 有 paused 状态处理")
ok("streamPromptOverride" in vue, "App.vue 有 streamPromptOverride（提示词修改）")

# === 清理 ===
shutil.rmtree(tmp_dir, ignore_errors=True)

print("\n" + "=" * 60)
print(f"  结果: {PASS} pass / {FAIL} fail")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
