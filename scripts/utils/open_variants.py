# -*- coding: utf-8 -*-
"""开局方案（动笔前定故事走向）：给 N 个结构不同的开局，用户选一个再写大纲。

## 为什么这一层要单独做

现有两份"多方案"都在**结构已成之后**：
- 大纲页签的「多方案对比」——全书大纲已经生成，用户挑节点拼合；
- 分章页签的「选写作方向」——章大纲已经出来，用户选基调/视角/节奏。

真正贵的返工是**方向错了**：那时全书大纲的钱已经烧完，改方向等于推倒重来。
所以这里在 stage2 之前先给 N 个走向，让用户拍板，选中的注入 stage2。

## 与分支样例的分工

| | 分支样例（已有） | 开局方案（本模块） |
|---|---|---|
| 时机 | 章大纲之后 | 全书大纲之前 |
| 粒度 | 一章的写法 | 整本书的走向 |
| 产物 | 样例段落 | 结构化走向（hook/spine/conflict/tone/risk） |

## 铁律：每张方案卡必须写"代价"

`risk` 字段是必填的。只写优点的三张卡等于没有选择 —— 用户看不出取舍。
prompt 里明确要求"诚实写这个方案容易写崩的地方"。
"""
import json
from datetime import datetime
from pathlib import Path

from utils import field_refine as fr
from utils.config_io import load_config_yaml
from utils.file_io import read_text, write_text
from utils.validator import parse_llm_json

VARIANT_COUNT = 3
MAX_TOKENS = 2400          # 三个方案的结构化文本，不需要长


def variants_path(root="."):
    """项目根下的大纲目录。**不要**用模块级 Path("data/...") ——
    `--root` 指向别的书档时，CWD 相对会静默读写错项目（本仓踩过同类坑）。"""
    return Path(root) / "data" / "outline" / "open_variants.json"


def chosen_path(root="."):
    return Path(root) / "data" / "outline" / "open_variant_chosen.md"


def _digest(setting_path, limit=6):
    """把设定集压成几十字的提示，别把整本 canon 塞进 prompt。"""
    try:
        data = json.loads(read_text(setting_path))
    except Exception:                                          # noqa: BLE001
        return "（设定集读取失败，按作者构想判断）"
    out = []
    for key, label in (("characters", "角色"), ("places", "地点"),
                       ("scenes", "场景"), ("concepts", "概念"), ("items", "物品")):
        names = [str((x or {}).get("name") or "").strip()
                 for x in (data.get(key) or []) if (x or {}).get("name")]
        names = [n for n in names if n][:limit]
        if names:
            out.append("%s：%s" % (label, "、".join(names)))
    return "；".join(out) or "（设定集为空）"


def _build_prompt(proj, setting_path, note):
    book = (proj or {}).get("book", {}) or {}
    user_outline = (book.get("user_outline") or "").strip() or "（未提供）"
    return f"""你是一名资深小说策划。作者要写一本 {book.get("genre") or "未定"} 类型的小说，
计划 {book.get("chapters") or "若干"} 章、约 {book.get("target_words") or "若干"} 字。

## 作者已有的构想

{user_outline}

## 素材里已确定的元素（不要改写它们，只决定怎么用）

{_digest(setting_path)}

## 任务

给 {VARIANT_COUNT} 个**结构上明显不同**的开局方案。

硬要求：
- 三个方案的**切入时机、叙事结构、核心矛盾**必须不同，不是同一故事换措辞。
- 每张卡必须诚实写 `risk`：这个方案**最容易写崩的地方**。只写优点的方案等于没有选择。
- 不许新增与素材冲突的设定（角色/地点/规则以素材为准）。
- 不写正文，只给结构化方案。

作者补充的偏好（若有）：{note or "（无）"}

## 输出格式

只输出一个 JSON 对象，不要任何其他文字、不要代码围栏：

{{"variants": [
  {{"title": "8 字内方案名",
    "hook": "读者看到的第一件事（一句话，具体动作或画面）",
    "spine": "主线一句话：谁要什么，什么挡着他",
    "conflict": "核心冲突：外在一句 + 内在一句",
    "tone": "基调（如：冷峻克制 / 荒诞 / 温情）",
    "risk": "这个方案最容易写崩的地方（诚实写代价）",
    "chapters_hint": "前 3 章怎么推进（各一句）"}}
]}}
"""


def generate(proj, setting_path="data/setting/setting.json", note="",
             client=None, cfg=None, db=None, run_id=None, root="."):
    """生成开局方案并落盘。返回 (ok, msg, payload)。"""
    if client is None:
        from utils.llm_client import make_client
        if cfg is None:
            from orchestrator import load_config
            cfg = load_config()
        client = make_client(cfg, "outliner")

    prompt = _build_prompt(proj, setting_path, note)
    from utils.llm_client import SYSTEM_PROMPT
    text, usage, model, finish = client._post_chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS)

    from utils import cost_tracker
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    cost = cost_tracker.estimate_cost_yuan(tin, tout, model)
    if db is not None:
        try:
            db.log_cost(run_id or "adhoc", "open_variants", 0, model, tin, tout, cost)
        except Exception as e:                                 # noqa: BLE001
            print(f"[open_variants] 记账失败（不影响产出）：{e}")

    if finish == "length":
        return False, ("输出被 max_tokens 截断（finish_reason=length）→ 方案不完整，已丢弃。"
                       "提高 open_variants.MAX_TOKENS 或换模型"), None

    data = parse_llm_json(text)
    variants = (data or {}).get("variants") if isinstance(data, dict) else None
    if not isinstance(variants, list) or not variants:
        return False, ("模型未按 JSON 返回方案（parse_llm_json 失败或 variants 为空）。"
                       "原文尾部: " + (text or "")[-160:]), None

    clean = []
    for i, v in enumerate(variants[:VARIANT_COUNT], 1):
        if not isinstance(v, dict):
            continue
        clean.append({
            "id": i,
            "title": str(v.get("title") or ("方案 %d" % i)).strip()[:20],
            "hook": str(v.get("hook") or "").strip(),
            "spine": str(v.get("spine") or "").strip(),
            "conflict": str(v.get("conflict") or "").strip(),
            "tone": str(v.get("tone") or "").strip(),
            "risk": str(v.get("risk") or "").strip(),
            "chapters_hint": str(v.get("chapters_hint") or "").strip(),
        })
    if len(clean) < 2:
        return False, "有效方案不足 2 个，重试一次", None
    # 缺 risk 的方案不能算数：没有代价的选项是假选项
    lacking = [v["title"] for v in clean if not v["risk"]]
    if lacking:
        return False, ("以下方案没写 risk（代价），按纪律判失败：%s。"
                       "重试；连续失败说明模型在回避取舍" % "、".join(lacking)), None

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "cost_yuan": cost,
        "variants": clean,
    }
    p = variants_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(payload, ensure_ascii=False, indent=2))
    return True, "已生成 %d 个开局方案" % len(clean), payload


def load_variants(root="."):
    p = variants_path(root)
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:                                          # noqa: BLE001
        return None


def pick(variant_id, root="."):
    """选中一个方案 → 写 open_variant_chosen.md（stage2 会读它）。"""
    data = load_variants(root)
    if not data:
        return False, "还没有生成过开局方案", None
    chosen = None
    for v in data.get("variants") or []:
        if int(v.get("id") or 0) == int(variant_id):
            chosen = v
            break
    if not chosen:
        return False, "没有 id=%s 的方案" % variant_id, None

    lines = [
        "# 作者选定的开局方案",
        "",
        "> 由「开局方案」卡片选定（%s）。stage2 生成全书大纲时必须按本方案的结构走；"
        % datetime.now().strftime("%Y-%m-%d %H:%M"),
        "> 与 project.yaml 的 user_outline 冲突时，**以本文件为准**。",
        "",
        "## 方案名",
        "",
        chosen.get("title", ""),
        "",
        "## 第一画面（hook）",
        "",
        chosen.get("hook", ""),
        "",
        "## 主线（spine）",
        "",
        chosen.get("spine", ""),
        "",
        "## 核心冲突",
        "",
        chosen.get("conflict", ""),
        "",
        "## 基调",
        "",
        chosen.get("tone", ""),
        "",
        "## 已知代价（写作时要盯住的地方）",
        "",
        chosen.get("risk", ""),
        "",
        "## 前 3 章节奏",
        "",
        chosen.get("chapters_hint", ""),
        "",
    ]
    cp = chosen_path(root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    write_text(cp, "\n".join(lines))
    return True, "已选定「%s」，stage2 会按它生成大纲" % chosen.get("title", ""), str(cp)


def load_chosen(root="."):
    """返回选中方案正文（没有则空串）。供 stage2 注入。"""
    p = chosen_path(root)
    if not p.exists():
        return ""
    try:
        return read_text(p).strip()
    except Exception:                                          # noqa: BLE001
        return ""


def clear_chosen(root="."):
    """放弃已选方案（重新挑或改回 user_outline 时用）。"""
    p = chosen_path(root)
    if p.exists():
        p.unlink()
        return True
    return False
