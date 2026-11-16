# -*- coding: utf-8 -*-
"""讨论设置（C19，2026-11-16）：AI 主动开关注 + 上下文窗口。

## 判据（每条都能变红）

- G1 默认读出来是“允许主动发言 / 12 轮”
- G2 越界值 400 且说清区间（不许静默落盘）
- G3 非整数 400
- G4 空请求 400（不许"什么都不改"却回 ok）
- G5 关掉主动发言后能读回 false
- G6 **写后注释还在**（system.yaml 里写着止烧口径与白名单理由，
      整份 dump 会把它们抹掉 —— 2026-10-31 踩过）
- G7 关掉主动发言 + 没写提示 → /discuss/ai_turn 400（AI 不许自己找话题）

用法：python tests/unit/test_discuss_config.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api                                                # noqa: E402
from nf_api_domains import project as dom_project            # noqa: E402
from nf_api_domains import discuss_thread as dom_thread      # noqa: E402
from utils import discussion                                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


class H:
    def __init__(self, headers=None):
        self.headers = headers or {}


def main():
    print("=" * 66)
    print("讨论设置（C19）")
    tmp = Path(tempfile.mkdtemp())
    orig_root = nf_api.ROOT
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    shutil.copy2(ROOT / "config" / "project.yaml", tmp / "config" / "project.yaml")
    nf_api.ROOT = tmp
    try:
        code, data = dom_project.handle_config_discuss(H())
        cur = (data or {}).get("current") or {}
        check("G1 默认允许主动发言", code == 200 and cur.get("ai_proactive") is True, (code, data))
        check("G1 默认窗口 12 轮", cur.get("session_keep") == 12, cur)

        code, data = dom_project.handle_config_discuss_set(H(), {"session_keep": 101})
        check("G2 越界 400 且说清区间",
              code == 400 and "0–100" in str(data.get("error")), (code, data))

        code, data = dom_project.handle_config_discuss_set(H(), {"session_keep": "abc"})
        check("G3 非整数 400", code == 400, (code, data))

        code, data = dom_project.handle_config_discuss_set(H(), {})
        check("G4 空请求 400", code == 400, (code, data))

        code, data = dom_project.handle_config_discuss_set(
            H(), {"ai_proactive": False, "session_keep": 6})
        check("G5 关闭 + 改窗口成功", code == 200, (code, data))
        code, data = dom_project.handle_config_discuss(H())
        cur = (data or {}).get("current") or {}
        check("G5 读回 false / 6",
              cur.get("ai_proactive") is False and cur.get("session_keep") == 6, cur)

        text = (tmp / "config" / "system.yaml").read_text(encoding="utf-8")
        check("G6 注释还在（没被整份 dump 冲掉）",
              "止烧" in text or "token_limit" in text, text[:80])
        check("G6 讨论段的说明也还在", "ai_proactive=true" in text or "讨论（2026-11-16" in text,
              "讨论段注释丢了")

        # G7：关掉主动发言后，AI 不许自己找话题
        discussion.open_thread(3, "主动闸", root=str(tmp))
        code, data = dom_thread.handle_discuss_ai_turn(
            H(), {"chapter": 3, "topic": "主动闸", "hint": ""})
        check("G7 关掉主动发言 + 空提示 → 400",
              code == 400 and data.get("error_kind") == "ai_proactive_off", (code, data))
        check("G7 拒文说清怎么继续", "先写一句" in str(data.get("error")), data.get("error"))
    finally:
        nf_api.ROOT = orig_root

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
