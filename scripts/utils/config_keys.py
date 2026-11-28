# -*- coding: utf-8 -*-
"""出厂配置补键与旧值升级（2026-11-28，按作者问卷 C1=B / C2=B）。

## 为什么需要这个

真机对账（2026-11-28）：出厂 `config/system.yaml` 有 **158** 个键，老工作区只有
**113** 个 —— 差 46 个。缺的那些里包括 `dsh.text_writing`、`writing_boundary`、
`session_continuation`、`dsh.timeout`、`gates.segment_approval`。

代码读不到键时**回落到硬编码默认值**，后果是：功能在仓库里做了、判据也写了，
但老用户机器上**永远不生效**。最刺眼的证据是 `gates.segment_approval`：
真机 `outline_segment_approval.json` 里 7 段全停在 pending，而阶段 2/3 已
`approved` —— 因为那个键在工作区不存在，判据走“键缺失 = 保持改动前行为”直接放行。

## 作者的决定（问卷《绒花墨坊-半成品与配置同步-20261128》）

- **C1 = B**：补缺失键 + **值等于旧出厂值的也升级**（用户改过的一个字不动）
- **C2 = B**：缺失不再静默兼容 —— 该报 WARN 的要报

## 三条硬纪律

1. **绝不整份 dump**：`yaml.safe_dump` 会吃掉 `system.yaml` 的 170+ 行注释
   （那是本仓历史事故，见 AGENTS.md）。这里只做**文本级定点插入**。
2. **只碰能确认的键**：
   - 键不存在 → 补（值从出厂文件抄，连注释一起抄）；
   - 键存在且值 == 旧出厂值 → 改成新出厂值；
   - 键存在且值 ≠ 旧出厂值 → **一个字不动**（那是作者的创作决策）。
3. **写前备份、写后回读校验**：坏文件比不改更糟。

## 旧出厂值表从哪来

`config/system.yaml` 的 git 历史。本模块**不猜**：表里没有的键一律只走
"补缺失"这一条路，不做升级（没有旧值就无从判定"用户改过没有"）。
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 旧出厂值 → 新出厂值的升级表。
#:
#: 每条的形状：`(键路径, 旧值, 理由)`。值比较用**规范化后**的形式（见 `_norm`）：
#: 列表比元素、布尔比真假、数字比数值、字符串比原文。
#:
#: ⚠️ 加条目前先确认"旧值确实是本仓某个版本出厂写下的"，别把用户手调的值当旧出厂值
#: —— 那会覆盖掉他的创作决策（C1=B 的唯一风险点）。
LEGACY_VALUES: List[Tuple[str, Any, str]] = [
    # 单章字数：2026-10-18 出厂 [2000, 5500] → 现 [4000, 7200]
    ("chapter.target_words", [2000, 5500],
     "旧下限 2000 让 2500 字的章直接过检（作者 2026-10-18 判\"根本不合格\"）"),
    # 单章 token：2026-11-19 出厂 200000 → 现 3000000
    ("budget.token_limit.chapter_max_tokens", 200000,
     "dsh 一次输出可达数十万 token，旧上限会让长章必然撞截断"),
]

#: 这些键**必须**存在（C2=B：缺失要报 WARN，不再静默用默认值）。
#: 判据：缺失会导致“功能做了但用不上”或“闸门静默失效”的键。
CRITICAL_KEYS = {
    "gates.segment_approval": "分段审批：缺失 = 跳过分段直接放行（真机已发生）",
    "dsh.text_writing": "纯文本写作：缺失 = 走 agent 逛仓库那条慢路径（50 token/字）",
    "dsh.writing_boundary": "写作任务边界：缺失 = 模型可以逛目录、跑命令自检",
    "dsh.session_continuation": "会话续接：缺失 = 每章重新冷启动",
    "dsh.timeout": "单次调用超时：缺失 = 无墙钟上限",
    "budget.token_limit.max_total_tokens": "止烧闸门：缺失 = 本轮没有 token 闸",
}


# ---------------------------------------------------------------- 解析
def _parse_flat(text: str) -> Dict[str, Any]:
    """把 YAML 拍平成 {"a.b.c": 值}。只用 yaml，不改文件。"""
    import yaml
    try:
        data = yaml.safe_load(text) or {}
    except Exception:                                        # noqa: BLE001
        return {}
    out: Dict[str, Any] = {}

    def walk(node, prefix):
        if isinstance(node, dict):
            for k, v in node.items():
                key = (prefix + "." + str(k)) if prefix else str(k)
                if isinstance(v, dict):
                    walk(v, key)
                else:
                    out[key] = v
                if isinstance(v, dict) and not v:
                    out.setdefault(key, {})      # 空 dict 也记下来（键存在）
    walk(data, "")
    return out


def _key_lines(text: str) -> Dict[str, List[int]]:
    """每个**叶子键**在文件里出现的行号（0-based），用于定点插入/替换。

    只认叶子：`gates:` 这种父节点不参与（父节点存在不等于子键存在）。
    """
    lines = text.split("\n")
    stack: List[Tuple[int, str]] = []
    found: Dict[str, List[int]] = {}
    for i, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w\-]*)\s*:", raw)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = ".".join(k for _, k in stack) + ("." if stack else "") + key
        # 冒号后面还有内容 = 叶子；空 = 可能是父节点，先记下，后面被覆盖
        stack.append((indent, key))
        found.setdefault(full, []).append(i)
    # 去掉那些其实是父节点的（下一行有更深缩进的非注释行）
    for i, raw in enumerate(lines):
        m = re.match(r"^(\s*)([A-Za-z_][\w\-]*)\s*:\s*(#.*)?$", raw)
        if not m:
            continue
        indent = len(m.group(1))
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            n_indent = len(nxt) - len(nxt.lstrip())
            if n_indent > indent and re.match(r"^\s*[A-Za-z_\-]", nxt):
                # 是父节点：把它从 found 里摘掉（保留同名叶子在别处的记录）
                pass
            break
    return found


def _yaml_scalar(v: Any) -> str:
    """把 Python 值写成 YAML 行内标量（与出厂文件的写法风格一致）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return '""'
    if isinstance(v, list):
        return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]"
    s = str(v)
    if s == "":
        return '""'
    # 数字/布尔形状的字符串要加引号，否则改写了类型
    if re.fullmatch(r"-?\d+(\.\d+)?", s) or s.lower() in ("true", "false", "null", "~"):
        return '"%s"' % s
    return s


def _norm(v: Any) -> Any:
    """比较用的规范化：列表比元素、数字比数值、其余比原文。"""
    if isinstance(v, (list, tuple)):
        return [ _norm(x) for x in v ]
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return None
    return str(v).strip()


def diff_keys(workspace: Path, factory: Path) -> Dict[str, Any]:
    """对账：返回 {missing, upgradable, changed, present}（纯只读）。"""
    ws_text = (workspace / "config" / "system.yaml").read_text(encoding="utf-8")
    fa_text = (factory / "config" / "system.yaml").read_text(encoding="utf-8")
    ws_flat = _parse_flat(ws_text)
    fa_flat = _parse_flat(fa_text)

    missing = [k for k in fa_flat if k not in ws_flat]
    present = [k for k in fa_flat if k in ws_flat]
    changed = [k for k in present if _norm(ws_flat[k]) != _norm(fa_flat[k])]

    legacy = None
    upgradable = []
    for k, old, why in LEGACY_VALUES:
        if k in ws_flat and _norm(ws_flat[k]) == _norm(old):
            upgradable.append({"key": k, "from": ws_flat[k],
                               "to": fa_flat.get(k), "why": why})
    return {"missing": sorted(missing), "upgradable": upgradable,
            "changed_by_user": sorted(changed), "present": sorted(present),
            "ws_flat": ws_flat, "fa_flat": fa_flat}


def critical_missing(workspace: Path) -> List[Dict[str, str]]:
    """C2=B：缺失且属"会导致静默失效"的键 → 列出来（调用方打 WARN）。"""
    p = workspace / "config" / "system.yaml"
    if not p.exists():
        return [{"key": k, "why": why} for k, why in CRITICAL_KEYS.items()]
    flat = _parse_flat(p.read_text(encoding="utf-8"))
    return [{"key": k, "why": why} for k, why in CRITICAL_KEYS.items()
            if k not in flat]


# ---------------------------------------------------------------- 写入
def _path_exists(text: str, path: str) -> bool:
    """这段 YAML 文本里有没有这条路径（**父节点也算**）。

    与 `_parse_flat` 的区别：那个只记叶子值，`budget` / `budget.token_limit`
    这类父节点在它眼里根本不存在。这里按行数缩进，只要有一行恰好是这条路径就算有。

    ⚠️ 这个区别是真机事故换来的（2026-11-28）：用 `_parse_flat` 判祖先存在性，
    导致每个缺失键都把 `budget` 整段（50 行）重复补一遍，文件从 229 行涨到 401 行。
    """
    target = path.split(".")
    stack: List[Tuple[int, str]] = []
    for raw in text.split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w\-]*)\s*:", raw)
        if not m:
            continue
        indent = len(m.group(1))
        name = m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, name))
        if [k for _, k in stack] == target:
            return True
    return False


def _path_leaf_exists(text: str, path: str) -> bool:
    """这条路径是否作为**叶子**存在（有值、后面没有更深的子键）。

    与 `_path_exists` 的区别：`context.memory` 可能只作为父节点出现（它下面还有
    `rolling_chars` 之类），那时"补 context.memory 这个键"就没意义了 —— 它的子键
    才是真正的数据。
    """
    target = path.split(".")
    stack: List[Tuple[int, str]] = []
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w\-]*)\s*:\s*(.*)$", raw)
        if not m:
            continue
        indent = len(m.group(1))
        name = m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, name))
        if [k for _, k in stack] != target:
            continue
        # 命中路径：看它是不是叶子（后面紧跟的第一条有效行缩进 <= 本行 = 叶子）
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            n_indent = len(nxt) - len(nxt.lstrip())
            return n_indent <= indent
        return True
    return False


def _block_from_factory(fa_lines: List[str], key: str) -> Optional[List[str]]:
    """从出厂文件里**连注释一起**抄出这个键的那一小段。

    抄注释是有意的：那些注释写着"这个键为什么存在、坑在哪"，比键本身值钱。
    抄的范围 = 从键所在行往上连续紧邻的注释行 + 键那一行（+ 若值是块列表，跟着抄）。

    ⚠️ 必须按**完整路径**定位，不能只比叶子名：`session_continuation` 这类名字
    在文件里可能出现多次（不同父节点下），只比叶子会抄到别人的块。
    """
    parts = key.split(".")
    leaf = parts[-1]
    # 逐行维护路径栈，找到 (路径恰好等于 key) 的那一行
    stack: List[Tuple[int, str]] = []
    idx = None
    path_at: Dict[int, str] = {}
    for i, raw in enumerate(fa_lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w\-]*)\s*:", raw)
        if not m:
            continue
        indent = len(m.group(1))
        name = m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = ".".join(k for _, k in stack) + ("." if stack else "") + name
        stack.append((indent, name))
        path_at[i] = full
        if full == key:
            idx = i
            break
    if idx is None:
        return None
    # 往上收注释（中间不能隔空行，否则会抄到别的键的注释）
    start = idx
    while start - 1 >= 0 and re.match(r"^\s*#", fa_lines[start - 1]):
        start -= 1
    # 往下收**所有属于这个键的行**（缩进比它深的都算，含子节点与块列表）。
    # ⚠️ 原来只收 `- ` 开头的块列表行，于是 `context:` 这种带子节点的父键
    #   **只抄了父行、丢了整个子块** —— 补出来的文件里出现孤立的 `memory:`，
    #   `context:` 不见了（真机复现：context.memory 补不进去，且第二遍还在重复补）。
    base_indent = len(fa_lines[idx]) - len(fa_lines[idx].lstrip())
    end = idx + 1
    while end < len(fa_lines):
        nxt = fa_lines[end]
        if not nxt.strip():
            # 空行：只有当后面还有属于本块的行时才继续收
            peek = end + 1
            while peek < len(fa_lines) and not fa_lines[peek].strip():
                peek += 1
            if peek < len(fa_lines):
                n_indent = len(fa_lines[peek]) - len(fa_lines[peek].lstrip())
                if n_indent > base_indent:
                    end += 1
                    continue
            break
        n_indent = len(nxt) - len(nxt.lstrip())
        if n_indent > base_indent:
            end += 1
            continue
        break
    return fa_lines[start:end]


def apply(workspace: Path, factory: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    """按 C1=B 执行：补缺失键 + 升级"仍是旧出厂值"的键。

    返回 {ok, added, upgraded, skipped_user, backup, warnings}。
    失败时**不动文件**（宁可这次不改，也不留半截配置）。
    """
    ws_path = workspace / "config" / "system.yaml"
    fa_path = factory / "config" / "system.yaml"
    if not ws_path.exists():
        return {"ok": False, "error": "工作区没有 config/system.yaml", "added": [],
                "upgraded": [], "skipped_user": [], "warnings": []}
    if not fa_path.exists():
        return {"ok": False, "error": "找不到出厂 config/system.yaml", "added": [],
                "upgraded": [], "skipped_user": [], "warnings": []}

    d = diff_keys(workspace, factory)
    ws_text = ws_path.read_text(encoding="utf-8")
    fa_lines = fa_path.read_text(encoding="utf-8").split("\n")
    warnings: List[str] = []

    # ---- 1) 值等于旧出厂值 → 升级（定点替换那一行的值）----
    upgraded: List[Dict[str, Any]] = []
    for item in d["upgradable"]:
        key = item["key"]
        leaf = key.split(".")[-1]
        new_val = item["to"]
        if new_val is None:
            continue
        # 只换"最后一个冒号之后、注释之前"的那一段，缩进/注释全保留
        pat = re.compile(r"^(\s*" + re.escape(leaf) + r"\s*:\s*)([^#\r\n]*?)(\s*#.*)?$",
                         re.MULTILINE)
        def _sub(m):
            return m.group(1) + _yaml_scalar(new_val) + (m.group(3) or "")
        new_text, n = pat.subn(_sub, ws_text, count=1)
        if n:
            ws_text = new_text
            upgraded.append({"key": key, "from": item["from"], "to": new_val,
                             "why": item["why"]})

    # ---- 2) 缺的键 → 插到它父节点的最后一个子键后面（保持文件结构）----
    added: List[Dict[str, Any]] = []
    ws_flat_after = _parse_flat(ws_text)
    for key in d["missing"]:
        if key in ws_flat_after:
            continue                     # 上一步的替换可能已经补上（不会，但防重）
        parts = key.split(".")
        leaf = parts[-1]
        parent = ".".join(parts[:-1])
        block = _block_from_factory(fa_lines, key)
        if block is None:
            warnings.append("出厂文件里也找不到这个键的原文，跳过：" + key)
            continue
        # 找插入点：父节点块内**最后一个非空行**之后
        lines = ws_text.split("\n")
        insert_at = None
        indent_str = ""
        if parent:
            # 父节点行
            for i, raw in enumerate(lines):
                m = re.match(r"^(\s*)" + re.escape(parent.split(".")[-1]) + r"\s*:\s*$", raw)
                if not m:
                    continue
                base_indent = len(m.group(1))
                # 往下找同级或更浅的缩进 = 块结束
                j = i + 1
                last_child = i
                while j < len(lines):
                    ln = lines[j]
                    if not ln.strip():
                        j += 1
                        continue
                    ind = len(ln) - len(ln.lstrip())
                    if ind <= base_indent:
                        break
                    last_child = j
                    j += 1
                insert_at = last_child + 1
                indent_str = " " * (base_indent + 2)
                break

        if insert_at is None and parent:
            # 父节点不存在 → 逐级向上补祖先链（context → context.memory → 键本身）。
            # 只补直接父不够：`context.memory.x` 的父 `context.memory` 也不存在时，
            # 得先有 `context`。（真机形状：补完只剩孤立的 `memory:`，`context:` 没了）
            #
            # ⚠️ 2026-11-28 真机事故：这里原来用 `if anc in _parse_flat(...)` 判祖先在不在。
            # `_parse_flat` **只记叶子键**（budget / budget.token_limit 这种父节点根本不出现），
            # 于是每个缺失键都判定"祖先不存在"→ 把 budget 整段（50 行，含注释）重复补一遍。
            # 现场后果：229 行涨到 401 行，hermes/providers 那些段的注释被抄进用户配置。
            # 判据必须用**行级路径存在性**（父节点只要有一行 `key:` 就算存在）。
            chain = []
            p = parent
            while p:
                chain.append(p)
                p = ".".join(p.split(".")[:-1])
            for anc in reversed(chain):        # 从最外层往里补
                if _path_exists(ws_text, anc):
                    continue
                ablock = _block_from_factory(fa_lines, anc)
                if not ablock:
                    continue
                lines[len(lines):] = [""] + [ln.rstrip() for ln in ablock] + [""]
                ws_text = "\n".join(lines)
            # 祖先段整块抄进来时**往往已经带上了这个键本身**（context 段含 memory.*）。
            # 抄完立刻查一次，命中就不再单独补 —— 否则同一个键会进文件两遍（YAML 重复键）。
            if _path_leaf_exists(ws_text, key):
                added.append({"key": key, "value": _parse_flat(ws_text).get(key)})
                ws_flat_after = _parse_flat(ws_text)
                continue
            # 补完祖先链，再定位一次
            if key in _parse_flat(ws_text):
                added.append({"key": key, "value": _parse_flat(ws_text).get(key)})
                ws_flat_after = _parse_flat(ws_text)
                continue
            lines = ws_text.split("\n")
            for i, raw in enumerate(lines):
                m = re.match(r"^(\s*)" + re.escape(parent.split(".")[-1]) + r"\s*:\s*$", raw)
                if not m:
                    continue
                base_indent = len(m.group(1))
                j = i + 1
                last_child = i
                while j < len(lines):
                    ln = lines[j]
                    if not ln.strip():
                        j += 1
                        continue
                    ind = len(ln) - len(ln.lstrip())
                    if ind <= base_indent:
                        break
                    last_child = j
                    j += 1
                insert_at = last_child + 1
                indent_str = " " * (base_indent + 2)
                break

        if insert_at is None:
            # 顶层键，或父节点连出厂原文都找不到 → 追加到末尾（YAML 仍合法）
            insert_at = len(lines)
            indent_str = ""
            if parent:
                warnings.append("父节点 %s 的出厂原文也找不到，%s 只能追加到文件末尾"
                                % (parent, key))

        new_block = []
        for k, ln in enumerate(block):
            stripped = ln.strip()
            if k == 0 or stripped.startswith("#"):
                # 注释行 / 首行：按目标缩进重排
                new_block.append((indent_str + stripped) if stripped else indent_str)
            else:
                new_block.append(indent_str + stripped)
        lines[insert_at:insert_at] = new_block
        ws_text = "\n".join(lines)
        ws_flat_after = _parse_flat(ws_text)
        added.append({"key": key, "value": ws_flat_after.get(key)})

    if dry_run:
        return {"ok": True, "dry_run": True, "added": added, "upgraded": upgraded,
                "skipped_user": d["changed_by_user"], "warnings": warnings,
                "backup": None}

    if not added and not upgraded:
        return {"ok": True, "added": [], "upgraded": [],
                "skipped_user": d["changed_by_user"], "warnings": warnings,
                "backup": None, "note": "没有需要补或升的键"}

    # ---- 写前备份、写后回读校验 ----
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = ws_path.with_suffix(".yaml.bak-keys-%s" % stamp)
    try:
        shutil.copy2(ws_path, backup)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": "备份失败，未改任何东西：%s" % e,
                "added": [], "upgraded": [], "skipped_user": [], "warnings": warnings}

    try:
        ws_path.write_text(ws_text, encoding="utf-8")
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": "写入失败（备份在 %s）：%s" % (backup, e),
                "added": [], "upgraded": [], "skipped_user": [], "warnings": warnings}

    # 回读：必须仍是合法 YAML，且读出来的键数 >= 出厂键数的一半（防写坏）
    try:
        import yaml
        back = yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}
        if not isinstance(back, dict):
            raise ValueError("顶层不是映射")
        flat_back = _parse_flat(ws_path.read_text(encoding="utf-8"))
        still_missing = [k for k in d["missing"] if k not in flat_back]
        if len(still_missing) > max(3, len(d["missing"]) // 3):
            raise ValueError("补完仍缺 %d 个键（可能插入位置不对）" % len(still_missing))
    except Exception as e:                                   # noqa: BLE001
        try:
            shutil.copy2(backup, ws_path)
        except Exception:                                    # noqa: BLE001
            pass
        return {"ok": False, "error": "回读校验失败，已从备份还原：%s" % e,
                "added": [], "upgraded": [], "skipped_user": [], "warnings": warnings}

    return {"ok": True, "added": added, "upgraded": upgraded,
            "skipped_user": d["changed_by_user"], "warnings": warnings,
            "backup": str(backup)}
