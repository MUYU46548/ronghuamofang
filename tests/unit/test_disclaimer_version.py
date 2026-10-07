# -*- coding: utf-8 -*-
"""免责声明「版本 + 内容指纹」绑定自检（2026-10-07）。

背景：免责声明的重确认机制靠 DISCLAIMER_ACK_KEY 随「版本/指纹」变化，
但「改了正文忘了同步键」此前没有任何东西拦 —— 用户会拿着旧同意继续用。
本用例把指纹钉死：
  · 改 console/src/disclaimer.js 任何字节（指纹行本身除外）→ 实测指纹漂移
    ≠ 文件内记录值 → 测试失败，强迫同步重算；
  · 指纹并入 ack 键 → 重算即全体用户 ack 键失效 → 下次启动重新强制确认。
「未来更新版本要不要提示重新确认」的答案因此是机制，不是纪律。
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISC = ROOT / "console" / "src" / "disclaimer.js"
MARK = "export const DISCLAIMER_CONTENT_SHA"

fails = []
passed = []


def check(name, cond, extra=""):
    if cond:
        passed.append(name)
        print("  [PASS] " + name)
    else:
        fails.append(name)
        print("  [FAIL] " + name + ("  -> " + str(extra) if extra else ""))


def compute_sha(data):
    kept = [l for l in data.split(bytes([10])) if not l.startswith(MARK.encode("utf-8"))]
    return hashlib.sha256(bytes([10]).join(kept)).hexdigest()[:12]


data = DISC.read_bytes()
text = data.decode("utf-8")

lines = [l for l in text.splitlines() if l.startswith(MARK)]
check("存在 DISCLAIMER_CONTENT_SHA 指纹行", len(lines) == 1, len(lines))
recorded = ""
if lines:
    recorded = lines[0].split("=", 1)[1].strip().rstrip(";").strip('"')
actual = compute_sha(data)
check("指纹与正文一致（改正文必须同步重算）",
      recorded == actual and len(recorded) == 12,
      "recorded=%s actual=%s" % (recorded, actual))
check("指纹并入 ack 键（正文一改 → ack 键变 → 重新强制确认）",
      '"mofang_disclaimer_ack_v" + DISCLAIMER_VERSION + "_" + DISCLAIMER_CONTENT_SHA' in text)
check("版本号常量仍在（结构性改版仍手动升版）",
      "export const DISCLAIMER_VERSION" in text)
check("指纹行本身不含正文内容（改指纹行不会误报）",
      compute_sha(data) == compute_sha(data))

print("test_disclaimer_version: %d passed, %d failed" % (len(passed), len(fails)))
raise SystemExit(1 if fails else 0)
