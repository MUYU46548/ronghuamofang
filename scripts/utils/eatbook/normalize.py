"""normalize —— 模型输出的宽容规整：中文键字段的类型收敛。

规整原则：只收敛类型、补默认值、丢弃未知键；不改动语义内容。
"""

from typing import Any, Dict, List

__all__ = ["as_str", "as_str_list", "as_rows"]


def as_str(value: Any) -> str:
    """收敛为字符串；None → ""，其余 str()。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def as_str_list(value: Any) -> List[str]:
    """收敛为字符串数组；列表逐项收敛，字典取键名，其余一律空数组。"""
    if isinstance(value, list):
        return [as_str(item) for item in value]
    if isinstance(value, dict):
        return [as_str(key) for key in value]
    return []


def as_rows(value: Any, keys: List[str], defaults: Dict[str, Any]) -> List[Dict[str, str]]:
    """把任意形状的行集合收敛为定键字典数组。

    - 列表项为字典 → 只保留 ``keys`` 中的键，缺失键补 ``defaults``；
    - 列表项为字符串 → 视为第一个键的值；
    - 字典形态（模型把数组写成了对象）→ 逐键展开，键名注入第一个键；
    - 其余形状 → 空数组。
    """
    first_key = keys[0] if keys else "名字"
    rows: List[Dict[str, str]] = []

    def _row_from(item: Any, name: str = "") -> Dict[str, str]:
        row = dict(defaults)
        if isinstance(item, dict):
            for key in keys:
                if key in item:
                    row[key] = as_str(item[key])
        elif name:
            # 字典形态 {"张三": "打了一架"}：键名→第一个键，值→第二个键
            row[first_key] = name
            if len(keys) > 1:
                row[keys[1]] = as_str(item)
        else:
            row[first_key] = as_str(item)
        return row

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                rows.append(_row_from(item))
            elif isinstance(item, str) and item.strip():
                rows.append(_row_from(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            rows.append(_row_from(item, name=as_str(key)))
    return rows
