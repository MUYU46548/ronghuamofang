# -*- coding: utf-8 -*-
"""止烧阈值语义自检（预设 / 校验 / 读配置时的坏值兜底，2026-10-03）。零 LLM。

用户第 1 条指令："阈值应该调高……另外我希望这些参数可以由用户在设置中手动配置
（并保留一套默认预设，防止用户乱改改坏）"。

本用例守三件事：
1. **预设就是出厂值**：`config/system.yaml` 里的四个键必须与 `TOKEN_LIMIT_PRESET`
   逐项一致 —— 否则设置页点「恢复默认」会写出一个与出厂不同的配置（很隐蔽的漂移）；
2. **改不坏**：越界/非数字/未知字段一律拒绝，且报错说清哪一项、区间是多少；
3. **改坏了也看得见**：手工把配置写成 0/天文数字时，读配置的归一化会**拉回预设并打印 WARN**，
   绝不让闸门静默消失（与本轮修的「hermes 下 ¥ 记账恒 0」同一类失败）。

用法：python tests/unit/test_token_limit_config.py
"""
import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.config_io import load_config_yaml, set_section_scalars  # noqa: E402
from utils.cost_tracker import (DEFAULT_TOKEN_LIMIT,  # noqa: E402
                                TOKEN_LIMIT_BOUNDS, TOKEN_LIMIT_PRESET,
                                normalize_token_limit, validate_token_limit)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def case_preset_matches_shipped():
    print("\n【1】出厂配置 == 预设（否则「恢复默认」会写出与出厂不同的值）")
    cfg = load_config_yaml(ROOT / "config" / "system.yaml")
    shipped = ((cfg.get("budget") or {}).get("token_limit")) or {}
    check("system.yaml 的 token_limit 与 TOKEN_LIMIT_PRESET 逐项一致",
          shipped == TOKEN_LIMIT_PRESET, "shipped=%s preset=%s" % (shipped, TOKEN_LIMIT_PRESET))
    check("累计上限 = 1000 万/轮（用户 2026-10-03 定调）",
          shipped.get("max_total_tokens") == 10000000, shipped.get("max_total_tokens"))
    check("单请求上限 = 50000（按单章 5000~15000 字留足余量）",
          shipped.get("per_request_max_tokens") == 50000, shipped.get("per_request_max_tokens"))
    check("预设默认不按次熔断 hermes（实测一次子会话就 1.8 万，8k 会误停）",
          shipped.get("per_request_pause_hermes") is False)
    check("预设是「开」的（止烧默认生效）", shipped.get("enabled") is True)
    check("DEFAULT_TOKEN_LIMIT（缺键兜底）仍是关闭态：不改变未配置时的老行为",
          DEFAULT_TOKEN_LIMIT["enabled"] is False
          and DEFAULT_TOKEN_LIMIT["max_total_tokens"] == 0)


def case_validate():
    print("\n【2】写入口校验：越界/坏值一律拒绝，且报错可行动")
    ok, msg, norm = validate_token_limit({"max_total_tokens": 10000000})
    check("合法单键 → 通过，其余按预设补齐",
          ok and norm["max_total_tokens"] == 10000000
          and norm["per_request_max_tokens"] == TOKEN_LIMIT_PRESET["per_request_max_tokens"], norm)

    for body, needle in (
            ({"max_total_tokens": 0}, "越界"),
            ({"max_total_tokens": -5}, "越界"),
            ({"max_total_tokens": TOKEN_LIMIT_BOUNDS["max_total_tokens"][1] + 1}, "越界"),
            ({"per_request_max_tokens": TOKEN_LIMIT_BOUNDS["per_request_max_tokens"][0] - 1},
             "越界"),
            ({"per_request_max_tokens": "abc"}, "必须是整数"),
            ({"warn_ratio": 0}, "越界"),
            ({"warn_ratio": 1.5}, "越界"),
            ({"whatever": 1}, "未知字段")):
        ok, msg, norm = validate_token_limit(body)
        check("拒绝 %s 并说明原因" % body, (not ok) and needle in msg, msg)
        check("被拒绝时不返回归一结果（避免调用方误用）", norm is None, norm)

    ok, msg, norm = validate_token_limit({"enabled": "false"})
    check("表单字符串 'false' 被正确当成布尔（设置页会传字符串）",
          ok and norm["enabled"] is False, norm)
    ok, msg, norm = validate_token_limit({"enabled": "1"})
    check("'1' → True", ok and norm["enabled"] is True, norm)
    ok, msg, norm = validate_token_limit({"max_total_tokens": " 20000000 "})
    check("带空白的字符串数字可解析", ok and norm["max_total_tokens"] == 20000000, norm)
    ok, msg, norm = validate_token_limit({"warn_ratio": "0.8"})
    check("warn_ratio 字符串可解析", ok and norm["warn_ratio"] == 0.8, norm)
    ok, msg, norm = validate_token_limit({"per_request_pause_hermes": "true"})
    check("按次熔断可开启（用户想开就开）",
          ok and norm["per_request_pause_hermes"] is True, norm)
    ok, msg, norm = validate_token_limit("not a dict")
    check("非对象 → 拒绝", (not ok) and "对象" in msg, msg)


def case_normalize_never_loses_brake():
    print("\n【3】读配置时的兜底：坏值回落到预设 + **可见告警**（闸门不许静默消失）")
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = normalize_token_limit({"enabled": True, "max_total_tokens": 5,
                                     "per_request_max_tokens": 9})
    warn = buf.getvalue()
    check("越界值被拉回预设", out["max_total_tokens"] == TOKEN_LIMIT_PRESET["max_total_tokens"]
          and out["per_request_max_tokens"] == TOKEN_LIMIT_PRESET["per_request_max_tokens"], out)
    check("打印了 WARN（改坏配置必须看得见）", "WARN" in warn and "越界" in warn, warn[:200])

    buf = io.StringIO()
    with redirect_stdout(buf):
        out2 = normalize_token_limit({"enabled": True, "max_total_tokens": 0,
                                      "per_request_max_tokens": 0})
    check("两项都 0（= 没有闸门）→ 累计上限回落预设 + 告警",
          out2["max_total_tokens"] == TOKEN_LIMIT_PRESET["max_total_tokens"]
          and "WARN" in buf.getvalue(), out2)

    check("显式只关单请求上限（0）而累计有值 → 尊重用户意图，不回落",
          normalize_token_limit({"enabled": True, "max_total_tokens": 2000000,
                                 "per_request_max_tokens": 0})["per_request_max_tokens"] == 0)
    check("enabled=false 时不动任何值（用户主动关的，不啰嗦）",
          normalize_token_limit({"enabled": False, "max_total_tokens": 0})["max_total_tokens"] == 0)
    check("空配置 → 老语义（关闭 + 无上限）",
          normalize_token_limit(None) == DEFAULT_TOKEN_LIMIT, normalize_token_limit(None))
    check("坏 warn_ratio 回落预设",
          normalize_token_limit({"warn_ratio": 0})["warn_ratio"]
          == TOKEN_LIMIT_PRESET["warn_ratio"])


def case_batch_write():
    print("\n【4】批量写：一次备份、一次落盘、四个值一起回读")
    tmp = Path(tempfile.mkdtemp(prefix="toklim_"))
    try:
        import os
        origin = os.getcwd()
        (tmp / "config").mkdir(parents=True, exist_ok=True)
        cfg = tmp / "config" / "system.yaml"
        cfg.write_text(
            "# 说明段落（必须保留）\n"
            "budget:\n"
            "  limit_yuan: 300\n"
            "  token_limit:\n"
            "    enabled: true\n"
            "    per_request_max_tokens: 8000\n"
            "    per_request_pause_hermes: false\n"
            "    max_total_tokens: 3000000\n"
            "    warn_ratio: 0.7\n"
            "gates:\n  agent_mode: false\n", encoding="utf-8")
        os.chdir(tmp)
        try:
            values = {"enabled": True, "per_request_max_tokens": 50000,
                      "per_request_pause_hermes": False, "max_total_tokens": 10000000,
                      "warn_ratio": 0.7}
            ok, msg = set_section_scalars(("budget", "token_limit"), values, path=cfg)
            check("批量写成功", ok, msg)
            back = ((load_config_yaml(cfg) or {}).get("budget") or {}).get("token_limit")
            check("五个值全部落地（含布尔）", back == values, back)
            text = cfg.read_text(encoding="utf-8")
            check("说明段落保留", "# 说明段落（必须保留）" in text)
            check("同一 section 的其它键保留", "limit_yuan: 300" in text)
            check("其它 section 保留", "agent_mode: false" in text)
            hist = list((tmp / "config" / "history").glob("*.yaml"))
            check("**只留 1 份备份**（批量写不该每键一份）", len(hist) == 1,
                  [h.name for h in hist])
        finally:
            os.chdir(origin)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_estimate_shows_gate():
    print("\n【5】预估工具必须给出 token 闸门对照（hermes 下 ¥ 恒 0，只能看 token）")
    sys.path.insert(0, str(ROOT / "scripts"))
    import estimate_tokens as et
    res = et.estimate(stages=[4], cfg=load_config_yaml(ROOT / "config" / "system.yaml"),
                      proj=load_config_yaml(ROOT / "config" / "project.yaml"),
                      use_history=False)
    tk = res.get("token_limit") or {}
    check("结果带 token_limit 块", bool(tk), tk)
    check("上限与配置一致", tk.get("max_total_tokens") == 10000000, tk)
    check("给出「预计跑完」的累计与该轮占比",
          isinstance(tk.get("projected_tokens"), int) and tk.get("projected_pct") is not None, tk)
    check("预计值 = 已用 + 本轮估算",
          tk.get("projected_tokens") == tk.get("used_tokens", 0) + tk.get("estimated_tokens", 0), tk)
    text = et.format_text(res)
    check("文本模式也打印 token 闸门行（不能只有 JSON 有）", "token 闸门" in text, text[-200:])
    check("文本里带上限数字", "10000000" in text, text[-200:])

    # 反证：把上限压到**校验下限 100000**（validate_token_limit 的最小值，
    # normalize 会把低于下限的配置回落预设——不能设得更小），并把本轮估算放大到必超下限。
    # 写死「估算 > 100000」会让本用例随仓库 data/ 漂移假红（2026-10-05 修：
    # 判据与数据解耦——重复投喂 stage4 直到估算 ≥ 200000，再用下限做对照）。
    est = (res.get("token_limit") or {}).get("estimated_tokens") or 0
    check("前提：本轮估算 > 0（否则反证无意义）", est > 0, est)
    repeat = max(1, -(-200000 // max(int(est), 1)))
    cfg = load_config_yaml(ROOT / "config" / "system.yaml")
    cfg["budget"]["token_limit"] = dict(cfg["budget"]["token_limit"])
    cfg["budget"]["token_limit"]["max_total_tokens"] = 100000
    res2 = et.estimate(stages=[4] * repeat, cfg=cfg,
                       proj=load_config_yaml(ROOT / "config" / "project.yaml"),
                       use_history=False)
    check("小上限 → exceeds=True", (res2.get("token_limit") or {}).get("exceeds") is True,
          res2.get("token_limit"))
    check("小上限 → 文本给出「跑完将撞闸门」",
          "撞闸门" in et.format_text(res2), et.format_text(res2)[-200:])


def main():
    print("=" * 62)
    print("  止烧阈值语义自检")
    print("=" * 62)
    case_preset_matches_shipped()
    case_validate()
    case_normalize_never_loses_brake()
    case_batch_write()
    case_estimate_shows_gate()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
