# -*- coding: utf-8 -*-
"""P0 验证：断点续跑 + eval 后门清除（离线自检）。"""
import io, sys, shutil, importlib, re, ast
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = 0, 0
def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + msg)
    else:
        FAIL += 1
        print("  [FAIL] " + msg)

# === 任务 1: is_chapter_complete ===
print("\n=== 任务 1：断点续跑粒度优化 ===")
vc = importlib.import_module("utils.verify_chapter")
is_chapter_complete = vc.is_chapter_complete

tmp_dir = ROOT / "_tmp_chapcheck"
tmp_dir.mkdir(exist_ok=True)
p = tmp_dir / "test.md"

p.write_text("", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "空文件判定为未完成")

p.write_text("## 第1章\n\n内容截断了...", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "截断标记判定为未完成")

p.write_text("## 第1章\n\n这里是TODO待填", encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "占位符判定为未完成")

p.write_text("## 第1章\n\n" + "a" * 50, encoding="utf-8")
ok(not is_chapter_complete(p, 100, 3000), "字数不足判定为未完成")

p.write_text("## 第1章 测试章节\n\n" + "你好世界" * 150, encoding="utf-8")
ok(is_chapter_complete(p, 100, 3000), "正常章节判定为完成")

ok(not is_chapter_complete(tmp_dir / "not_exist.md", 100, 3000), "文件不存在判定为未完成")

# === 任务 6: eval 后门清除 ===
print("\n=== 任务 6：安全加固（eval 后门） ===")
src = (ROOT / "scripts" / "price_wizard.py").read_text(encoding="utf-8")

# 过滤：排除注释行、字符串字面量中的 eval
# 只检查代码逻辑行（非字符串内容）
def has_real_eval(source):
    """检测源码中是否真有 eval() 调用（排除 ast.literal_eval、字符串内的 eval）。"""
    # 移除 ast.literal_eval
    cleaned = source.replace("ast.literal_eval", "")
    # 移除字符串字面量
    cleaned = re.sub(r'""".*?"""', '""', cleaned, flags=re.S)
    cleaned = re.sub(r"'''.*?'''", "'''", cleaned, flags=re.S)
    cleaned = re.sub(r'"[^"]*"', '""', cleaned)
    cleaned = re.sub(r"'[^']*'", "''", cleaned)
    # 只检查非注释行
    code_lines = [l for l in cleaned.splitlines() if l.strip() and not l.strip().startswith("#")]
    return any("eval(" in l for l in code_lines)

ok(not has_real_eval(src), "price_wizard.py 代码中不再使用 eval()")
ok("ast.literal_eval(clean)" in src, "改为 ast.literal_eval()")

# 安全测试
malicious = '__import__("os").system("echo pwned")'
try:
    ast.literal_eval(malicious)
    ok(False, "ast.literal_eval 拒绝了恶意输入")
except (ValueError, SyntaxError):
    ok(True, "ast.literal_eval 拒绝函数调用/属性访问")

normal = '{"model_a": {"in": 1.0, "out": 2.0}}'
result = ast.literal_eval(normal)
ok(result == {"model_a": {"in": 1.0, "out": 2.0}}, "正常 dict literal 正常解析")

# === stage4 集成验证 ===
print("\n=== stage4 集成验证 ===")
src_s4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
ok("from utils.verify_chapter import" in src_s4 and "is_chapter_complete" in src_s4,
   "stage4 导入了 is_chapter_complete")
ok("verified = [n for n in completed if is_chapter_complete" in src_s4,
   "stage4 使用 is_chapter_complete 过滤半成品")

# === nf_api /chapters/verify 端点 ===
print("\n=== nf_api /chapters/verify 端点 ===")
src_api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
ok('elif p == "/chapters/verify":' in src_api, "nf_api 新增 /chapters/verify 端点")
ok("is_chapter_complete(path" in src_api, "端点使用 is_chapter_complete 校验")

# === 清理 ===
shutil.rmtree(tmp_dir, ignore_errors=True)

print(f"\n=== 结果: {PASS} pass / {FAIL} fail ===")
sys.exit(0 if FAIL == 0 else 1)
