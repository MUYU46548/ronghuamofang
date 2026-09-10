# -*- coding: utf-8 -*-
"""价格向导（可选工具）：交互式增删改 cost_tracker.RATES。

用途：换厂商 / 模型降价时，无需手搓 Python dict，按提示输入即可。
      改动即时写入 scripts/utils/cost_tracker.py，下次运行即生效。

用法：
  .venv/Scripts/python.exe scripts/price_wizard.py
"""
import re
import sys
from pathlib import Path

COST_TRACKER = Path(__file__).resolve().parent / "utils" / "cost_tracker.py"
RATES_START = "RATES = {"
RATES_END = "}"

# 尝试导入当前 RATES 作为默认值
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from utils.cost_tracker import RATES as CURRENT_RATES
except ImportError:
    CURRENT_RATES = {}


def read_source():
    return COST_TRACKER.read_text(encoding="utf-8")


def write_source(text):
    COST_TRACKER.write_text(text, encoding="utf-8")


def parse_rates(source):
    """从源码中解析 RATES dict（简单 eval，仅内部使用）。"""
    start = source.find(RATES_START)
    if start < 0:
        return None
    # 找匹配的闭合括号
    depth = 0
    end = start
    for i, ch in enumerate(source[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    block = source[start:end]
    # 去掉 "RATES = " 前缀，只留 dict 字面量
    dict_start = block.index("{")
    dict_block = block[dict_start:]
    # 去掉注释行和行内注释（eval 不处理注释）
    lines = []
    for line in dict_block.splitlines():
        if "#" in line:
            line = line[:line.index("#")]
        lines.append(line)
    clean = "\n".join(lines).strip()
    # 安全 eval：只允许 dict 字面量
    try:
        rates = eval(clean, {"__builtins__": {}}, {})
        if isinstance(rates, dict):
            return rates
    except Exception:
        pass
    return None


def format_rate_entry(name, rate, max_name_len):
    """格式化单条 RATES 条目。"""
    parts = []
    for k in ("in", "out", "cache_read"):
        if k in rate:
            parts.append(f'"{k}": {rate[k]}')
    inner = ", ".join(parts)
    pad = " " * (max_name_len - len(name) + 1)
    comment = ""
    # 保留原注释（如果有）
    return f'    "{name}": {pad}{{{inner}}},{comment}'


def rebuild_rates_block(rates):
    """重建 RATES = {...} 源码块。"""
    lines = ["RATES = {"]
    max_name = max((len(repr(k)) for k in rates.keys()), default=0)
    for name, rate in rates.items():
        # 保留原注释
        comment = ""
        lines.append(format_rate_entry(name, rate, max_name) + comment)
    lines.append("}")
    return "\n".join(lines)


def prompt_float(prompt, default=None):
    """提示输入浮点数，回车保留默认值。"""
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  请输入数字（如 1.5）")


def prompt_choice(prompt, choices):
    """提示选择。"""
    print(prompt)
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    while True:
        raw = input("  选: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print(f"  输入 1-{len(choices)}")


def main():
    print("=" * 50)
    print("  绒花墨坊 · 价格向导")
    print("=" * 50)
    print()
    print(f"  目标文件: {COST_TRACKER}")
    print()

    source = read_source()
    rates = parse_rates(source)
    if rates is None:
        print("  [ERROR] 无法解析 RATES，请检查 cost_tracker.py 格式")
        return 1

    print(f"  当前共 {len(rates)} 个模型：")
    for name, rate in rates.items():
        cr = rate.get("cache_read", "—")
        print(f"    {name:25s} in={rate['in']:6} out={rate['out']:6} cache_read={cr}")
    print()

    action = prompt_choice("  操作：", ["新增模型", "修改模型", "删除模型", "退出"])

    if action == "退出":
        return 0

    if action == "新增模型":
        name = input("  模型名（如 deepseek-v4.1-flash）: ").strip()
        if not name:
            print("  取消")
            return 0
        if name in rates:
            print(f"  {name} 已存在，请用「修改」")
            return 0
        in_price = prompt_float("  输入单价（元/百万 token）")
        out_price = prompt_float("  输出单价（元/百万 token）")
        has_cache = input("  有 cache_read 价格吗？(y/n) [n]: ").strip().lower()
        rate = {"in": in_price, "out": out_price}
        if has_cache == "y":
            rate["cache_read"] = prompt_float("  cache_read 单价（元/百万 token）")
        rates[name] = rate
        print(f"  已添加 {name}")

    elif action == "修改模型":
        name = input("  模型名: ").strip()
        if name not in rates:
            print(f"  {name} 不存在，请用「新增」")
            return 0
        cur = rates[name]
        print(f"  当前: in={cur['in']} out={cur['out']} cache_read={cur.get('cache_read', '无')}")
        in_price = prompt_float(f"  输入单价（回车保留 {cur['in']}）", cur["in"])
        out_price = prompt_float(f"  输出单价（回车保留 {cur['out']}）", cur["out"])
        rate = {"in": in_price, "out": out_price}
        if "cache_read" in cur:
            cr = prompt_float(f"  cache_read 单价（回车保留 {cur['cache_read']}）", cur["cache_read"])
            rate["cache_read"] = cr
        else:
            add = input("  添加 cache_read 价格吗？(y/n) [n]: ").strip().lower()
            if add == "y":
                rate["cache_read"] = prompt_float("  cache_read 单价")
        rates[name] = rate
        print(f"  已更新 {name}")

    elif action == "删除模型":
        name = input("  模型名: ").strip()
        if name not in rates:
            print(f"  {name} 不存在")
            return 0
        confirm = input(f"  确认删除 {name}？(y/n) [n]: ").strip().lower()
        if confirm == "y":
            del rates[name]
            print(f"  已删除 {name}")
        else:
            print("  取消")
            return 0

    # 重建源码
    new_block = rebuild_rates_block(rates)
    # 替换原 RATES 块
    start = source.find(RATES_START)
    depth = 0
    end = start
    for i, ch in enumerate(source[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    new_source = source[:start] + new_block + source[end:]
    write_source(new_source)

    print()
    print("  ✅ 已写入 cost_tracker.py")
    print("  下次运行即生效（已运行的流水线不受影响）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  取消")
        sys.exit(0)
