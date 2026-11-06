# -*- coding: utf-8 -*-
"""C2：直连引擎凭据缺失 → **启动即失败**，且报错能指导修复。

## 病（2026-11-06 之前）

`make_client` 在 `engine: direct` 且 `api_key_env` 没有值时只打一行 WARN：
"客户端构造成功，实际调用时才会报错"。于是：
- 失败点在**第一轮真请求**，401 埋在某个阶段深处；
- 界面上只看到"阶段失败"，看不出是"没配 Key"；
- 用户会去翻模型、翻提示词、翻素材，全都不对。

## 本用例锁什么

1. 缺凭据 → 抛 `MissingCredentialError`（不是 WARN、不是等调用时才炸）；
2. 报错里必须点名**环境变量名**并给修法（.env / hermes / self），
   且**不出现任何密钥值**；
3. `NF_ALLOW_MISSING_KEY=1` 时放行（离线预演 / 估算要用）；
4. `engine: hermes` 不需要 Key —— 不许误伤。

用法：python tests/unit/test_missing_credential.py
"""
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []
ENV_NAME = "NF_TEST_MISSING_KEY_C2"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def _cfg(engine="direct"):
    return {
        "engine": engine,
        "model": {"default": {"provider": "p1", "id": "m1"}},
        "providers": {"p1": {
            "type": "openai-compat",
            "base_url": "https://example.invalid/v1",
            "api_key_env": ENV_NAME,
        }},
    }


def main():
    from utils.llm_client import MissingCredentialError, make_client

    os.environ.pop(ENV_NAME, None)
    os.environ.pop("NF_ALLOW_MISSING_KEY", None)

    print("\n【1】缺凭据 → 启动即失败")
    msg = ""
    try:
        make_client(_cfg(), "default", verbose=False)
        raised = None
    except MissingCredentialError as e:
        raised = e
        msg = str(e)
    except SystemExit as e:                                   # 不许退化成 SystemExit
        raised, msg = None, str(e)
    check("抛 MissingCredentialError（不是静默构造成功）", raised is not None)
    check("报错点名环境变量", ENV_NAME in msg, msg.splitlines()[:1])
    check("报错给出三条修法（.env / hermes / self）",
          ".env" in msg and "hermes" in msg and "self" in msg)
    check("报错不含任何密钥值（只写变量名）",
          "=" not in msg.split(ENV_NAME, 1)[-1].splitlines()[0] or "XXX_API_KEY" in msg,
          msg)

    print("\n【2】NF_ALLOW_MISSING_KEY=1 → 放行（离线预演用）")
    os.environ["NF_ALLOW_MISSING_KEY"] = "1"
    try:
        c = make_client(_cfg(), "default", verbose=False)
        check("放行后能构造客户端", c is not None, type(c).__name__)
    except Exception as e:                                    # noqa: BLE001
        check("放行后能构造客户端", False, "%s: %s" % (type(e).__name__, e))
    finally:
        os.environ.pop("NF_ALLOW_MISSING_KEY", None)

    print("\n【3】hermes 引擎不需要 Key（不许误伤）")
    try:
        c = make_client(_cfg("hermes"), "default", verbose=False)
        check("hermes 正常构造", c is not None, type(c).__name__)
    except Exception as e:                                    # noqa: BLE001
        check("hermes 正常构造", False, "%s: %s" % (type(e).__name__, e))

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
