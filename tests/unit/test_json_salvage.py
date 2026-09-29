# -*- coding: utf-8 -*-
"""解析层容错自检（件2，2026-09-29）。

覆盖两处修复：
A. `utils.validator.parse_llm_json` —— `<think>` 内嵌剥离 / 断裂（截断）JSON 兜底 /
   失败不抛裸异常且回报原文偏移；同时回归既有「围栏 / 数组 / 已是 dict」语义。
B. `OpenAICompatClient._salvage_answer` —— content 为空时从 reasoning_content
   捞回 JSON（审稿断裂根因）；**纯散文思考仍必须丢弃**（守住 2026-09-23 的修复：
   绝不把思考当正文写进小说）。

全程离线、零网络、零费用。
用法：python tests/unit/test_json_salvage.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import validator                                       # noqa: E402
from utils.validator import parse_llm_json, strip_think, extract_json  # noqa: E402
from utils.llm_client import OpenAICompatClient                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def no_raise(name, fn):
    """恶意样本底线：任何输入都不得抛裸异常。返回 (ok, result_or_exc)。"""
    try:
        return True, fn()
    except Exception as e:                                         # noqa: BLE001
        check(name, False, "抛异常: " + type(e).__name__ + ": " + e.__class__.__name__)
        return False, e


# ---------------------------------------------------------------- A. validator

def test_validator_regression():
    print("\n[A1] parse_llm_json 既有语义回归（不得因新容错而变）")
    check("裸对象", parse_llm_json('{"findings":[{"a":1}]}') == {"findings": [{"a": 1}]})
    check("```json 围栏", parse_llm_json('```json\n{"findings":[]}\n```') == {"findings": []})
    check("围栏外寒暄", parse_llm_json('好的：\n```json\n{"ok":true}\n```\n完毕') == {"ok": True})
    check("裸数组", parse_llm_json('[{"chapter":1}]') == [{"chapter": 1}])
    check("空输入 → None", parse_llm_json("") is None and parse_llm_json(None) is None)
    check("已是 dict 原样返回", parse_llm_json({"already": "dict"}) == {"already": "dict"})
    check("非 JSON → None", parse_llm_json("这不是 JSON，只是普通文字。") is None)


def test_think_strip():
    print("\n[A2] <think> 内嵌剥离（R1 系网关把思考内嵌 content）")
    check("成对标签被剥离", strip_think("<think>先想一想</think>{\"a\":1}") == '{"a":1}')
    check("<thinking> 变体", strip_think("<thinking>x</thinking>1") == "1")
    check("内嵌后仍可解析",
          parse_llm_json('<think>推理…</think>\n{"ok":true}') == {"ok": True})
    check("只有开标签（截断）→ 其后全丢",
          parse_llm_json('<think>这里全是没写完的思考 {"ok":') is None)


def test_truncated_repair():
    print("\n[A3] 断裂 JSON 兜底（max_tokens 截断 / 中途收尾）")
    s = '{"chapters":[{"n":1,"findings":[]}'
    v = parse_llm_json(s)
    check("缺两个收尾括号可救回", isinstance(v, dict) and v.get("chapters") == [{"n": 1, "findings": []}],
          repr(v))
    v2 = parse_llm_json('[{"n":1},{"n":2}')
    check("数组元素截断可救回", isinstance(v2, list) and len(v2) == 2, repr(v2))
    check("括号已配对时不做修复（正常文本不受影响）",
          parse_llm_json('{"a":1}') == {"a": 1})


def test_malicious_no_raise():
    print("\n[A4] 恶意/畸形样本：3/3 类样本不得抛裸异常")
    samples = [
        "[{'a':1}]",          # 单引号
        "{",                  # 只有一个开括号
        "{{{{",               # 全是开括号
        "[[[[[[",             # 未闭合数组
        "}",                  # 只有闭括号
        '{"a": 1,}',          # 对象尾逗号（当前不修，但不得抛）
        '{"a": "未闭合',       # 字符串未闭合
        "<think>只有开标签",    # think 未闭合
        "", None, b"\xff\xfe",  # 空 / bytes
        "纯文字，无任何括号。",
    ]
    ok_all, results = True, []
    for s in samples:
        ok, r = no_raise("样本不抛异常", lambda s=s: parse_llm_json(s))
        if not ok:
            ok_all = False
        results.append(r)
    check("畸形样本全部安全返回（无裸异常）", ok_all)
    check("畸形样本要么 None 要么是 dict/list",
          all(r is None or isinstance(r, (dict, list)) for r in results))


def test_error_offset():
    print("\n[A5] 失败回报原文偏移（parse_llm_json.LAST_ERROR）")
    parse_llm_json("前言 {bad} 后记")
    err = validator.LAST_ERROR
    check("LAST_ERROR 为字典且含 offset", isinstance(err, dict) and "offset" in err, err)
    check("偏移指向首个 JSON 起点（前言 + 空格 + { → 3）",
          isinstance(err, dict) and err.get("offset") == 3, err)
    parse_llm_json("没有括号")
    check("无 JSON 起点时 offset == -1",
          isinstance(validator.LAST_ERROR, dict) and validator.LAST_ERROR.get("offset") == -1,
          validator.LAST_ERROR)
    v, off = extract_json("前缀 {\"a\":1} 后缀")
    check("extract_json 返回解析值 + 起点偏移", v == {"a": 1} and off == 3, (v, off))


# ---------------------------------------------------------- B. _salvage_answer

def test_salvage():
    print("\n[B1] reasoning_content 兜底：只认协议块 / 真 JSON，散文一律丢弃")
    check("协议块优先（原行为不变）",
          OpenAICompatClient._salvage_answer('废话\n===FILE: data/x.md===\n正文') .startswith("===FILE:"))
    rj = '先分析一下。\n{"chapters":[{"n":1,"findings":[]}]}'
    got = OpenAICompatClient._salvage_answer(rj)
    check("reasoning-only JSON 可捞回", got.startswith('{"chapters"') and '"n":1' in got, got[:80])
    prose = 'The user says "第 2 次调用仅处理..." —— 我应该输出正文。'
    check("纯散文思考 → 空（守住 09-23「思考不进正文」）",
          OpenAICompatClient._salvage_answer(prose) == "")
    prose2 = "我的计划是：{先写场景}，然后收尾。"
    check("含花括号但非 JSON 的思考 → 空",
          OpenAICompatClient._salvage_answer(prose2) == "", OpenAICompatClient._salvage_answer(prose2))


def main():
    print("===== 解析层容错自检（件2）=====")
    test_validator_regression()
    test_think_strip()
    test_truncated_repair()
    test_malicious_no_raise()
    test_error_offset()
    test_salvage()
    print("\n===== 合计: %d passed, %d failed =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
