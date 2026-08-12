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


def load_template(template_name, variables=None):
    """加载 prompts/{template_name}，返回 (meta dict, body str)。

    variables: dict，键将替换正文中的 {{key}}。未提供的占位符保留原样。
    """
    path = Path("prompts") / template_name
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
    return meta, body
