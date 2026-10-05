# -*- coding: utf-8 -*-
"""配置类 YAML 的**唯一**读取/定向写入入口（带重复键检测）。

## 为什么要有这个模块（2026-10-03）

PyYAML 对**重复键静默取后值** —— 不报错、不告警。本项目已经在这上面栽过两次：

1. `project.yaml` 的 `user_outline` 写了两遍 → 真实大纲被空值覆盖（2026-09-28，
   已由 `utils.project_config._UniqueKeyLoader` 修掉，但**只修了那一个文件**）；
2. `system.yaml` 的 `gates.agent_mode` 同时写了 true 与 false → 生效的是后者，
   而界面/代码看着"设过了"（2026-09-23，`nfctl check` 的重复键检测就是为此加的
   —— 但**检测**不等于**拒绝**，loader 照样静默覆盖）。

2026-10-03 又把止烧阈值（`budget.token_limit.*`）放进了 `system.yaml`：
配置被静默覆盖 = **熔断闸门形同虚设**。所以这里把判据收成一份，
并让所有配置读取走它（`tests/unit/test_config_strict_loader.py` 钉住"不许再有裸
`safe_load` 读 config"）。

## 分工（刻意切干净）

- **本模块**：管「字节 ↔ 结构」——读（严格）、写（定向改写，保留注释）、备份、回读校验。
- **`cost_tracker`**：管**语义**——token 阈值的默认预设、归一化、合法性校验。
  两边都不重复对方那份判据。
"""
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from utils.file_io import read_text, write_text

ROOT = Path(__file__).resolve().parents[2]

SYSTEM_YAML = "config/system.yaml"
LOCAL_YAML = "config/system.local.yaml"
PROJECT_YAML = "config/project.yaml"
BACKUP_DIR = "config/history"


class ConfigError(ValueError):
    """配置文件有重复键 / 格式错误 / 写入后回读不一致。

    刻意继承 ValueError：调用方老代码里 `except Exception` / `except ValueError`
    都能接住，且能被 CLI 层收敛成"一行可行动的话"而不是 traceback。
    """


class _UniqueKeyLoader(yaml.SafeLoader):
    """重复键 → 抛错（PyYAML 默认静默取后值）。"""


def _construct_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConfigError(
                "YAML 重复键 '%s'（第 %d 行）：PyYAML 默认会静默取后一个值，"
                "本项目显式拒绝 —— 否则「看着设过了、实际生效的是另一行」"
                "这类坑无法被发现。请删掉多余那一行；"
                "体检：python scripts/nfctl.py check" % (key, key_node.start_mark.line + 1))
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_config_yaml(path, default=None):
    """严格读取一个配置 YAML。

    - 文件不存在：返回 default（缺省 None）—— 「未初始化」是合法状态，不该抛。
    - 重复键 / 语法错误：抛 `ConfigError`（带行号与修法）。
    - **相对路径 = CWD 项目优先，缺失才回退脚本仓根**（与
      `agent_guard.load_project_config` 同一判据，2026-10-05 统一）。
      为什么不是「固定解到仓根」：数据侧（progress.json / data/ 产物）全是 **CWD 相对**
      = 正在操作的那个项目；配置若固定读脚本仓根，就是「按 B 项目的配置写 A 项目的数据」。
      2026-10-04 A0 把相对路径一律解到仓根，本为修非仓库根 CWD 的 FileNotFoundError，
      却顺带架空了所有依赖 chdir 的调用方（审稿重试 / 自动重写两组测试同源挂掉即此）。
    """
    p = Path(path)
    if not p.is_absolute():
        cwd_p = Path.cwd() / p
        p = cwd_p if cwd_p.exists() else (ROOT / p)
    if not p.exists():
        if isinstance(default, Exception):
            raise default
        return default
    text = read_text(p)
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except ConfigError:
        raise
    except yaml.YAMLError as e:
        raise ConfigError("解析 %s 失败：%s　（请检查缩进/冒号；"
                          "体检：python scripts/nfctl.py check）"
                          % (p, str(e).replace("\n", " ")[:200])) from e
    return data


def deep_merge(base, override):
    """深度合并两个 dict，override 优先（列表整体替换，不做元素级拼接）。"""
    result = dict(base or {})
    for k, v in (override or {}).items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_pipeline_config(system_path=SYSTEM_YAML, local_path=LOCAL_YAML,
                         project_path=PROJECT_YAML):
    """流水线的标准配置入口。返回 (cfg, proj)。

    cfg = system.yaml ← system.local.yaml（深合并，local 优先）；
    proj = project.yaml。三者全部走严格 loader。
    """
    cfg = load_config_yaml(system_path) or {}
    local = load_config_yaml(local_path) or {}
    if local:
        cfg = deep_merge(cfg, local)
    proj = load_config_yaml(project_path) or {}
    return cfg, proj


# ---------------------------------------------------------------------------
# 定向写入（保留注释与排版）——给「设置」页用
#
# 为什么不定向 dump：`yaml.safe_dump` 会把注释、空行、键顺序全丢掉，
# 而 system.yaml 里大量注释是**使用说明**（阈值为什么这么设、哪个键在什么引擎下无效）。
# 与 `utils/project_config.set_book_fields` 同一套做法：按行替换 + 备份 + 写后回读校验。
# ---------------------------------------------------------------------------

def _indent_of(line):
    return len(line) - len(line.lstrip())


def _block_range(lines, idx, indent):
    """以 lines[idx]（缩进 indent）为块首，返回块体范围 [lo, hi)。"""
    hi = len(lines)
    for i in range(idx + 1, len(lines)):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if _indent_of(ln) <= indent:
            hi = i
            break
    return idx + 1, hi


def _find_key(lines, key, lo, hi, indent):
    """在 [lo, hi) 内找**恰好缩进为 indent** 的 `key:` 行，返回行号或 -1。"""
    for i in range(lo, hi):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            continue
        if _indent_of(lines[i]) == indent and s.startswith(key + ":"):
            return i
    return -1


def _render_scalar(value):
    """把一个标量渲染成**YAML 正确**的一行（布尔用 true/false，字符串该引号就引号）。

    为什么不用 `str(value)`：`str(False)` = "False"，而 YAML 里合法写法是 `false`
    —— 写进去能被解析，但回读比对会不一致（会把一次成功写入误报成失败）。
    同理 `"true"`（字符串）必须写成 `'true'`，否则读回来变成布尔值（**静默改类型**）。

    多行值（块标量）不在本 API 范围内：直接拒绝，避免写出一行装不下的东西。
    """
    dumped = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True)
    # PyYAML 给**裸标量**会附一个文档结束标记 `...`（引号标量则不会）→ 去掉它，
    # 只留一行真正的值。多行（块标量）不在本 API 范围内：直接拒绝。
    parts = [ln for ln in dumped.split("\n") if ln.strip() and ln.strip() != "..."]
    if len(parts) != 1:
        raise ConfigError("只支持单行标量值（多行请手工编辑配置）：%r" % (value,))
    return parts[0]


def _apply_one(lines, section_path, key, value, comment=None, allow_insert=True):
    """在内存里的 lines 上改写 `section_path.key`。返回 (ok, msg)。"""
    lo, hi, indent = 0, len(lines), -1
    missing_from = None
    for level in section_path:
        want = indent + 2 if indent >= 0 else 0
        idx = _find_key(lines, level, lo, hi, want)
        if idx < 0:
            missing_from = (level, lo, hi, want)
            break
        indent = want
        lo, hi = _block_range(lines, idx, indent)

    rendered = _render_scalar(value)

    if missing_from is not None:
        if not allow_insert:
            return False, "配置里没有 %s（且不允许插入）" % ".".join(section_path)
        level_key, ins_lo, ins_hi, ins_indent = missing_from
        pos = ins_hi
        # 回退跳过块尾的空行，插在块体最后一行之后（保持排版紧凑）
        while pos > ins_lo and not lines[pos - 1].strip():
            pos -= 1
        cur_indent = ins_indent
        new_lines = [" " * ins_indent + level_key + ":"]
        for deeper in section_path[section_path.index(level_key) + 1:]:
            cur_indent += 2
            new_lines.append(" " * cur_indent + deeper + ":")
        cur_indent += 2
        line = " " * cur_indent + key + ": " + rendered
        if comment:
            line += "  # " + str(comment).replace("\n", " ")
        new_lines.append(line)
        lines[pos:pos] = new_lines
        return True, rendered

    idx = _find_key(lines, key, lo, hi, indent + 2)
    line = " " * (indent + 2) + key + ": " + rendered
    if comment:
        line += "  # " + str(comment).replace("\n", " ")
    if idx >= 0:
        lines[idx] = line
    else:
        pos = hi
        while pos > lo and not lines[pos - 1].strip():
            pos -= 1
        lines[pos:pos] = [line]
    return True, rendered


def set_section_scalars(section_path, values, path=SYSTEM_YAML, comments=None,
                        allow_insert=True):
    """**一次写多个键**：一次备份、一次落盘、一次回读校验。

    为什么要批量：设置页保存 4 个阈值时若逐个调用单键版，会落 4 份备份、
    写 4 次盘，中途失败还会留下"改了一半"的配置。判据（定位/渲染/回读）与单键版**共用**
    （`set_section_scalar` 就是它的薄包装），不重复实现。

    返回 (ok, msg)。
    """
    if isinstance(section_path, str):
        section_path = (section_path,)
    comments = comments or {}
    p = Path(path)
    if not p.exists():
        return False, "配置文件不存在: " + str(p)
    lines = read_text(p).split("\n")
    rendered = {}
    for key, value in values.items():
        ok, res = _apply_one(lines, section_path, key, value,
                             comment=comments.get(key), allow_insert=allow_insert)
        if not ok:
            return False, res
        rendered[key] = res

    backup = None
    try:
        bdir = Path(BACKUP_DIR)
        bdir.mkdir(parents=True, exist_ok=True)
        backup = bdir / ("%s_%s.yaml" % (p.stem, datetime.now().strftime("%Y%m%d_%H%M%S")))
        shutil.copy2(p, backup)
    except OSError:
        backup = None

    write_text(p, "\n".join(lines))
    # 写后回读校验：严格 loader + **每个值都真落地且类型一致**（写坏就报错 + 指出备份）
    try:
        back = load_config_yaml(p) or {}
        node = back
        for level in section_path:
            node = (node or {}).get(level) or {}
    except Exception as e:                                  # noqa: BLE001
        return False, ("写入后回读失败（文件可能已写坏，备份在 %s）：%s"
                       % (backup, str(e)[:160]))
    bad = {k: (v, node.get(k)) for k, v in values.items() if node.get(k) != v}
    if bad:
        return False, ("写入后回读不一致（期望/实际：%s；备份在 %s）"
                       % ("；".join("%s %r≠%r" % (k, a, b) for k, (a, b) in bad.items()),
                          backup))
    return True, "已更新 %s：%s%s" % (
        ".".join(section_path),
        "、".join("%s=%s" % (k, rendered[k]) for k in values),
        ("（备份 %s）" % backup.name) if backup else "")


def set_section_scalar(section_path, key, value, path=SYSTEM_YAML,
                       comment=None, allow_insert=True):
    """把 `section_path.key` 定向改写为 value（保留注释与其它内容）。

    `section_path` 可以是字符串（顶层段）或元组（嵌套段，如 `("budget", "token_limit")`）——
    配置里绝大多数设置住在嵌套段下，只支持顶层段等于没法用。

    - 已存在该键 → 整行替换（旧的尾注释一并替换，避免留下与实际值矛盾的说明）。
    - 段/键不存在且 allow_insert → 按层级缩进补建（`budget:` → `  token_limit:` → `    key: v`）。
    - 写前备份到 config/history/，写后**用严格 loader 回读校验**（按值比较，不按字符串比）。

    返回 (ok: bool, msg: str)。实现是 `set_section_scalars` 的薄包装（判据只有一份）。
    """
    return set_section_scalars(section_path, {key: value}, path=path,
                               comments={key: comment} if comment else None,
                               allow_insert=allow_insert)
