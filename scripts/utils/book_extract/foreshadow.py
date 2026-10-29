"""模块⑥ 伏笔登记+追踪（台账化，只记增量；读②）。

出门版提示卡见 ``prompts/06_伏笔登记与追踪.md``；行结构契约见
``schemas/foreshadow_ledger.schema.json``。

本地台账 :class:`Ledger` 负责存账与合并：模型增量对账只返回"变化的行"，
``Ledger.apply`` 按 ``伏笔`` 名（去首尾空白）匹配合并，匹配不到视为新伏笔入账。
"""

import json
from typing import Any, Callable, Dict, List, Union

from . import core
from .normalize import as_str

__all__ = ["PROMPT_TEMPLATE", "STATUSES", "Ledger", "build_prompt",
           "build_register_prompt", "build_update_prompt", "parse_response", "run"]

PROMPT_TEMPLATE = """两步任务：

第一步（登记）：从材料中找出所有伏笔，输出台账 JSON 数组，每条格式：
{"伏笔": "", "埋设位置": "", "内容": "", "状态": "未回收|已回收|推进中", "回收位置": ""}

第二步（对账增量）：对照新章节，逐条判断既有台账中每个伏笔是否有推进/回收/矛盾：
- 有推进 → 该行状态改"推进中"，在"内容"末尾追加一句增量说明（注明新章节位置）
- 已回收 → 该行状态改"已回收"，"回收位置"填新章节位置
- 发现与新章节矛盾 → 该行"内容"末尾追加"【矛盾】…"说明
只输出发生变化的行（含新发现的伏笔作为新行），不重复输出未变化的条目。输出格式与第一步相同。
只输出 JSON 数组本身，不要输出任何解释文字或代码块标记。

既有台账（第一次建账没有台账就只做第一步）：
---
[[台账]]
---

摘要/正文材料（第一步登记依据）：
---
[[材料]]
---

新章节（第二步对账依据）：
---
[[新章节]]
---"""

STATUSES = ("未回收", "已回收", "推进中")
_ROW_KEYS = ("伏笔", "埋设位置", "内容", "状态", "回收位置")
_ROW_DEFAULTS = {"伏笔": "", "埋设位置": "", "内容": "", "状态": "", "回收位置": ""}


def normalize_row(item: Any) -> Dict[str, str]:
    """把一行收敛为台账行形状；非法状态保留原文，交由调用方审。"""
    row = dict(_ROW_DEFAULTS)
    if isinstance(item, dict):
        for key in _ROW_KEYS:
            if key in item:
                row[key] = as_str(item[key])
    elif isinstance(item, str):
        row["伏笔"] = item
    return row


def build_prompt(ledger: Union[str, List[Dict[str, str]]], material: str, new_chapter: str) -> str:
    """完整三槽版：台账 + 材料 + 新章节。"""
    if isinstance(ledger, str):
        ledger_text = ledger
    else:
        ledger_text = json.dumps(ledger, ensure_ascii=False, indent=2)
    return core.fill(PROMPT_TEMPLATE, 台账=ledger_text, 材料=material, 新章节=new_chapter)


def build_register_prompt(material: str) -> str:
    """第一次建账：只做第一步登记。"""
    return build_prompt("（空，第一次建账）", material, "（无）")


def build_update_prompt(ledger: Union[str, List[Dict[str, str]]], new_chapter: str) -> str:
    """日常增量：只做第二步对账（材料槽留空）。"""
    return build_prompt(ledger, "（无，本次仅对账）", new_chapter)


def parse_response(text: str) -> List[Dict[str, str]]:
    """解析模型输出为台账行数组（登记与增量共用同一行结构）。"""
    data = core.extract_json(core.strip_fences(text))
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = max(lists, key=len) if lists else []
    if not isinstance(data, list):
        raise core.JSONExtractionError("模块⑥ 期望一个 JSON 数组，实际不是")
    return [normalize_row(item) for item in data]


def run(ledger: Union[str, List[Dict[str, str]]], material: str, new_chapter: str,
        call: Callable[[str], str]) -> List[Dict[str, str]]:
    """build → call → parse 一步到位。"""
    if call is None:
        raise TypeError("run() 需要注入 call(prompt)->str 的 LLM 调用函数；参考实现不内置网络请求")
    return parse_response(call(build_prompt(ledger, material, new_chapter)))


class Ledger:
    """伏笔台账：本地存账 + 增量合并（只记变化，不重算全库）。"""

    def __init__(self, rows: Union[str, List[Dict[str, str]], None] = None) -> None:
        if rows is None:
            self.rows: List[Dict[str, str]] = []
        elif isinstance(rows, str):
            self.rows = parse_response(rows)
        else:
            self.rows = [normalize_row(r) for r in rows]

    # -- 查询 -------------------------------------------------------------
    def get(self, name: str) -> Dict[str, str]:
        """按伏笔名（去首尾空白）精确取行；无则返回 None。"""
        key = as_str(name).strip()
        for row in self.rows:
            if row["伏笔"].strip() == key:
                return row
        return None

    # -- 增量合并 ----------------------------------------------------------
    def apply(self, changes: Union[str, List[Dict[str, str]]]) -> Dict[str, List[str]]:
        """把模型对账返回的"变化的行"合并进台账。

        匹配键 = ``伏笔`` 名（去首尾空白）。合并只覆盖模型给出的非空字段
        （只记变化，空字段不抹掉既有值）；未命中视为新伏笔入账，状态缺省
        "未回收"。返回 ``{"added": [...], "updated": [...]}``。
        """
        if isinstance(changes, str):
            changes = parse_response(changes)
        added: List[str] = []
        updated: List[str] = []
        for change in changes:
            name = as_str(change.get("伏笔") if isinstance(change, dict) else change).strip()
            if not name:
                continue
            existing = self.get(name)
            row = normalize_row(change)
            if existing is None:
                if not row["状态"]:
                    row["状态"] = "未回收"
                self.rows.append(row)
                added.append(name)
            else:
                for key in _ROW_KEYS:
                    if row[key]:
                        existing[key] = row[key]
                updated.append(name)
        return {"added": added, "updated": updated}

    # -- 持久化 ------------------------------------------------------------
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.rows, ensure_ascii=False, indent=indent)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    @classmethod
    def from_file(cls, path: str) -> "Ledger":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(fh.read())
