# -*- coding: utf-8 -*-
"""提示词模板加载器（v2 4.3：模板与代码分离，可热更新）。

模板文件位于 prompts/，结构：YAML frontmatter（stage/model/max_tokens/temperature）
+ Markdown 正文；正文占位符用 {{var}} 形式，加载时填充。
"""
import re
from pathlib import Path

import yaml

from utils.file_io import read_text

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


# 正文里的占位符（`{{名字}}`）。用于「填充后是否还有残留」的自检。
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# 已就「残留占位符」告警过的模板（同一进程只刷一次，避免逐章刷屏）
_warned_leftovers = set()


def load_template(template_name, variables=None):
    """加载 prompts/{template_name}，返回 (meta dict, body str)。

    variables: dict，键将替换正文中的 {{key}}。

    ## 两道防线（2026-10-01）

    ① **文件缺失 = 明确报错**：原先直接抛裸 `FileNotFoundError`，用户只看到
       一个临时路径，不知道该怎么办。现在带上可执行的恢复方式。
       （为什么不做"第二套内置预设"：payload 里的 `prompts/` 本身就是预设 ——
       Electron 升级会 force 覆盖回来。再加一份源码内副本只会引入
       "到底哪套生效"的新歧义，而真正缺的是**恢复入口**。）

    ② **残留占位符 = 告警**：模板里写了 `{{xxx}}` 但调度方没提供同名变量时，
       旧的实现会把它**原样发给模型** —— 既白烧 token，又可能让模型照着
       `{{xxx}}` 的字面去写。这是典型的静默失败，现在至少留下痕迹。
       刻意只告警不抛：占位符是否必需由各 stage 决定，这里不替它们做判断。
    """
    path = Path("prompts") / template_name
    if not path.exists():
        raise FileNotFoundError(
            "提示词模板缺失: prompts/" + str(template_name)
            + "　恢复方式：① GUI「提示词」页签 → 该模板 → 选历史备份回滚；"
              "② git checkout -- prompts/" + str(template_name))
    text = read_text(path)
    meta = {}
    body = text
    m = FRONTMATTER_RE.match(text)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        body = text[m.end():]
    if variables:
        for key, value in variables.items():
            body = body.replace("{{" + key + "}}", str(value))

    leftovers = sorted(set(PLACEHOLDER_RE.findall(body)))
    if leftovers and template_name not in _warned_leftovers:
        _warned_leftovers.add(template_name)
        print("[template_loader] WARN " + str(template_name) + " 填充后仍残留占位符: "
              + "、".join(leftovers) + "（调度方未提供同名变量 —— 会原样发给模型，"
              "请检查模板里的变量名是否写错）")
    return meta, body
