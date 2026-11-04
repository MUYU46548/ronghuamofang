# -*- coding: utf-8 -*-
"""服务商（provider）的增删改 —— 只有这里写 `config/system.yaml` 的 `providers` 段。

## 为什么单独一个模块（2026-11-04，作者第 8 条）

原话：“服务商与 API Key 的界面是什么鬼？只有这两坨小众预设吗（甚至我自己都不用了）？
人家 Hermes 还内置几十家预设呢。而且还他妈不能自定义，怎么反而还倒退了？”

界面上确实只有两张只读卡片 + “填入 Key”：`providers` 段只能**手改 yaml**。
本模块补上“新增 / 编辑 / 删除”，并把常见服务商做成预设（一键填表）。

## 三条硬纪律（与 config_io 的分工）

1. **不许整份 `yaml.dump` 覆盖写** —— `system.yaml` 有 170+ 行注释（止烧口径、
   白名单理由、思考模式实测记录），一次 dump 全没了，且用户不会收到任何提示。
   所以这里是**定点块写入**：只替换/插入 `providers.<id>` 那一段，其余行逐字不动。
2. **模型名不预填**（除注释示例外的空列表）：模型名会过期，写死一批等于
   第一次调用就 404（2026-10-09 glm-5 下线就是这么炸的）。自定义 provider
   新建时 `available_models` 留空，让作者用界面上的“同步服务商模型”拉。
3. **删除要有理由拒绝**：正在被 `model.*.provider` 或 `engine` 使用的 provider
   不许删（删了下一次运行直接找不到配置）。要删先切走。

## 写入形态

```yaml
providers:
  myvendor:                     ← 新增的插在 providers: 之后（不动别人的注释）
    type: openai-compat
    base_url: https://api.example.com/v1
    api_key_env: MYVENDOR_API_KEY
    timeout: 600
    retries: 5
    available_models: []
    fallback: []
```

`api_key_env` 是**变量名**，真 Key 走 `.env`（`POST /env/set` 有键名白名单，
会自动带上新 provider 的变量名）。
"""
import re
from pathlib import Path

from utils.config_io import load_config_yaml
from utils.file_io import read_text, write_text

PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,39}$")
#: 只收 OpenAI 兼容端点 —— 本项目的客户端按 `base_url + "/chat/completions"` 拼，
#: 不兼容的协议（Anthropic 原生 / Gemini 原生）接进来只会 404，进预设等于挖坑。
PRESETS = [
    {"id": "deepseek", "name": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"},
    {"id": "moonshot", "name": "月之暗面 Kimi",
     "base_url": "https://api.moonshot.cn/v1", "api_key_env": "MOONSHOT_API_KEY"},
    {"id": "zhipu", "name": "智谱 BigModel",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPU_API_KEY"},
    {"id": "dashscope", "name": "阿里百炼（通义）",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "api_key_env": "DASHSCOPE_API_KEY"},
    {"id": "siliconflow", "name": "硅基流动",
     "base_url": "https://api.siliconflow.cn/v1", "api_key_env": "SILICONFLOW_API_KEY"},
    {"id": "openrouter", "name": "OpenRouter",
     "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    {"id": "openai", "name": "OpenAI",
     "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    {"id": "xai", "name": "xAI Grok",
     "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY"},
    {"id": "groq", "name": "Groq",
     "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    {"id": "mistral", "name": "Mistral",
     "base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    {"id": "together", "name": "Together AI",
     "base_url": "https://api.together.xyz/v1", "api_key_env": "TOGETHER_API_KEY"},
    {"id": "volcengine", "name": "火山方舟（豆包）",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "api_key_env": "ARK_API_KEY"},
]

#: 新建 provider 时写进去的固定字段（其余可选项留给用户手改 yaml）
FIXED_DEFAULTS = {"type": "openai-compat", "timeout": 600, "retries": 5,
                  "available_models": [], "fallback": []}


def _system_yaml(root):
    return Path(root) / "config" / "system.yaml"


def _load(root):
    p = _system_yaml(root)
    if not p.exists():
        raise FileNotFoundError("config/system.yaml 不存在: %s" % p)
    return load_config_yaml(p) or {}


def _mask(v):
    v = str(v or "")
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return v[:3] + "*" * 6 + v[-4:]


def _key_state(env_name):
    """Key 是否已配置 + 掩码（**只读环境变量，不读 .env 文件内容**）。

    掩码用的是进程内的值 —— `/env/set` 写盘后会同步刷新 `os.environ`，
    所以“填入 Key”之后这里立刻就能看到“已配置”，不用重启。
    """
    import os
    raw = os.environ.get(env_name or "") or ""
    return bool(raw), _mask(raw)


def _active_map(cfg):
    """哪个 provider 正在被谁用：`{"writer": "tokenhub", ...}`。"""
    out = {}
    for role, m in (cfg.get("model") or {}).items():
        if isinstance(m, dict) and m.get("provider"):
            out[role] = m.get("provider")
    return out


def list_providers(root="."):
    """给界面用的归一化视图（不返回任何明文 Key）。"""
    cfg = _load(root)
    provs = cfg.get("providers") or {}
    active = _active_map(cfg)
    items = []
    for pid, p in provs.items():
        if not isinstance(p, dict):
            continue
        has_key, mask = _key_state(str(p.get("api_key_env") or ""))
        items.append({
            "id": pid,
            "type": p.get("type") or "openai-compat",
            "base_url": p.get("base_url") or "",
            "api_key_env": p.get("api_key_env") or "",
            "models": list(p.get("available_models") or []),
            "has_key": has_key,
            "key_mask": mask,
            "in_use_by": sorted([r for r, v in active.items() if v == pid]),
            "is_preset": any(pr["id"] == pid for pr in PRESETS),
        })
    return {"ok": True, "items": items, "presets": PRESETS,
            "active": active}


# ---------------------------------------------------------------- 定点块写入
def _providers_span(lines):
    """`providers:` 段的 [start, end) 行号（end = 下一个顶层键或文件尾）。"""
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^providers:\s*$", ln.rstrip("\r")):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        raw = lines[j].rstrip("\r")
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):     # 回到顶层 → 段结束
            end = j
            break
    return start, end


def _id_span(lines, start, end, pid):
    """段内 `  <pid>:` 那个子块的 [a, b) 行号；没有则 None。"""
    head = re.compile(r"^  %s:\s*(#.*)?$" % re.escape(pid))
    a = None
    for i in range(start + 1, end):
        if head.match(lines[i].rstrip("\r")):
            a = i
            break
    if a is None:
        return None
    b = end
    for j in range(a + 1, end):
        raw = lines[j].rstrip("\r")
        if not raw.strip():
            continue
        # 同级子键（两个空格缩进）开始 → 本块结束；注释行跟随上一条，不切
        if re.match(r"^  \S", raw) and not raw.lstrip().startswith("#"):
            b = j
            break
    return a, b


def _render_block(pid, data):
    """把一个 provider 渲染成 2 空格缩进的 YAML 行（不含行尾换行）。"""
    import yaml
    dumped = yaml.safe_dump({pid: data}, allow_unicode=True,
                            default_flow_style=False, sort_keys=False)
    return ["  " + ln if ln.strip() else ln for ln in dumped.rstrip("\n").split("\n")]


def _render_value(value):
    """单行 YAML 值（复用 config_io 的渲染，避免 `...` 文档结束符这类坑）。"""
    from utils.config_io import _render_scalar
    return _render_scalar(value)


def _field_span(lines, a, b, key):
    """块内 `    key:` 字段的 [i, j)（j = 下一个同级键 / 块尾）。没有则 None。

    ⚠️ **注释归下一条字段**：`    # 说明` 这种块内注释夹在两个字段之间时，
    必须留在原地（j 停在注释行，而不是越过它）—— 否则替换上一个字段的值会连注释一起吞掉。
    第一版就是这么吃掉 `tokenhub` 里那条“⚠️ 这条注释属于 tokenhub”的。
    """
    pat = re.compile(r"^    %s:" % re.escape(key))
    i = None
    for k in range(a + 1, b):
        if pat.match(lines[k].rstrip("\r")):
            i = k
            break
    if i is None:
        return None
    j = b
    comment_start = None
    for k in range(i + 1, b):
        raw = lines[k].rstrip("\r")
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            if comment_start is None:
                comment_start = k
            continue
        if re.match(r"^    \S", raw):
            j = comment_start if comment_start is not None else k
            break
    else:
        if comment_start is not None:
            j = comment_start
    return i, j


def _validate(pid, base_url, env_name, models):
    if not PROVIDER_ID_RE.match(pid or ""):
        return ("id 只能用 2-32 位小写字母/数字/下划线/连字符，且以字母或数字开头"
                "（例：deepseek、my-vendor）")
    if not str(base_url or "").startswith(("http://", "https://")):
        return "base_url 必须以 http:// 或 https:// 开头"
    if any(c in str(base_url) for c in " \t\r\n"):
        return "base_url 不能含空白"
    if "chat/completions" in str(base_url):
        return ("base_url 填到 `/v1` 就行，不要带 `/chat/completions`"
                "（程序会自己拼）")
    if not ENV_NAME_RE.match(env_name or ""):
        return ("api_key_env 必须是环境变量名（2-40 位大写字母/数字/下划线，"
                "以字母开头，例：DEEPSEEK_API_KEY）")
    if models is not None:
        if not isinstance(models, list):
            return "models 必须是字符串列表"
        for m in models:
            if not str(m or "").strip():
                return "模型名不能为空"
            if len(str(m)) > 80:
                return "模型名过长（≤80 字符）：%s" % str(m)[:30]
    return ""


def _rewrite_fields(lines, a, b, data, models):
    """在**已有**块内逐字段定点改写（保留块里的注释与手调字段）。

    ## 为什么不整块替换（第一版就栽在这）
    整块替换会把块内的注释一起吞掉 —— 实测 `tokenhub` 那段里有一条
    “⚠️ 这条注释属于 tokenhub”，改一次地址就没了。注释正是这个项目最贵的资产
    （止烧口径、思考模式实测记录都写在里面），所以只动**值行**：
    存在的字段替换、缺的字段补在 pid 行之后、其余行（含注释）原样保留。
    """
    edits, missing = [], []
    for key in ("type", "base_url", "api_key_env", "available_models",
                "timeout", "retries", "fallback"):
        if key not in data:
            continue
        if key == "available_models" and models is None:
            continue                      # 没传就保留用户手写清单
        seg = _field_span(lines, a, b, key)
        line = "    %s: %s" % (key, _render_value(data[key]))
        if seg:
            edits.append((seg[0], seg[1], [line]))
        else:
            missing.append(line)
    for i, j, new in sorted(edits, reverse=True, key=lambda x: x[0]):
        lines = lines[:i] + new + lines[j:]
    if missing:
        lines = lines[:a + 1] + missing + lines[a + 1:]
    return lines


def save_provider(root=".", pid="", base_url="", api_key_env="", models=None):
    """新增或更新一个 provider。返回 `(ok, msg, item)`。

    更新时**保留**原有的可选字段（disable_thinking_models / fallback / timeout…），
    只覆盖 id / base_url / api_key_env / available_models 这四项 ——
    用户手调过的思考模式名单不该因为改个地址就被抹掉。
    """
    raw_pid = str(pid or "").strip()
    if raw_pid != raw_pid.lower():
        # 大小写敏感：`MyVendor` 与 `myvendor` 会成为两个 key（模型下拉里出现两行一样的名字）
        return False, "id 必须全小写（大小写不同会变成两个不同的服务商）", None
    pid = raw_pid
    base_url = str(base_url or "").strip().rstrip("/")
    api_key_env = str(api_key_env or "").strip()
    err = _validate(pid, base_url, api_key_env, models)
    if err:
        return False, err, None

    path = _system_yaml(root)
    cfg = _load(root)
    provs = cfg.get("providers") or {}
    old = provs.get(pid) if isinstance(provs.get(pid), dict) else {}
    data = dict(FIXED_DEFAULTS)
    data.update(old)                       # 保留用户手调过的字段
    data["type"] = "openai-compat"
    data["base_url"] = base_url
    data["api_key_env"] = api_key_env
    if models is not None:
        seen, clean = set(), []
        for m in models:
            s = str(m).strip()
            if s and s not in seen:
                seen.add(s)
                clean.append(s)
        data["available_models"] = clean

    if not path.exists():
        return False, "config/system.yaml 不存在: %s" % path, None
    raw = read_text(path)
    lines = raw.split("\n")
    span = _providers_span(lines)
    if span is None:
        return False, ("config/system.yaml 里没有 `providers:` 段 —— "
                       "请先手工添加一行 `providers:` 再重试"), None
    start, end = span
    found = _id_span(lines, start, end, pid)
    if found:
        a, b = found
        new_lines = _rewrite_fields(lines, a, b, data, models)
        action = "已更新"
    else:
        block = _render_block(pid, data)
        new_lines = lines[:start + 1] + block + lines[start + 1:]
        action = "已新增"

    out = "\n".join(new_lines)
    # 回读校验：整份还能解析 + 这个 provider 的字段对得上（写坏就还原）
    write_text(path, out)
    try:
        back = load_config_yaml(path) or {}
        got = (back.get("providers") or {}).get(pid) or {}
        ok_back = (got.get("base_url") == base_url
                   and got.get("api_key_env") == api_key_env)
    except Exception as e:                                  # noqa: BLE001
        ok_back = False
        got = {}
        err = "%s: %s" % (type(e).__name__, e)
    if not ok_back:
        write_text(path, raw)
        return False, ("写后回读校验失败，已还原原文件"
                       + ("（%s）" % err if err else "")), None

    has_key, mask = _key_state(api_key_env)
    return True, ("%s服务商 %s（%s）" % (action, pid, base_url)), {
        "id": pid, "base_url": base_url, "api_key_env": api_key_env,
        "models": data.get("available_models") or [],
        "has_key": has_key, "key_mask": mask,
    }


def remove_provider(root=".", pid=""):
    """删除一个 provider。在用的一律拒（要先切走）。返回 `(ok, msg)`。"""
    pid = str(pid or "").strip().lower()
    cfg = _load(root)
    provs = cfg.get("providers") or {}
    if pid not in provs:
        return False, "没有这个服务商：%s" % pid
    used = sorted([r for r, v in _active_map(cfg).items() if v == pid])
    if used:
        return False, ("服务商 %s 正在被 %s 使用，先切到别的服务商再删"
                       % (pid, "、".join(used)))
    path = _system_yaml(root)
    raw = read_text(path)
    lines = raw.split("\n")
    span = _providers_span(lines)
    if span is None:
        return False, "config/system.yaml 里没有 `providers:` 段"
    start, end = span
    found = _id_span(lines, start, end, pid)
    if not found:
        return False, "没找到 %s 的配置块（可能格式与预期不同，请手工删）" % pid
    a, b = found
    write_text(path, "\n".join(lines[:a] + lines[b:]))
    try:
        back = load_config_yaml(path) or {}
        ok_back = pid not in ((back.get("providers") or {}))
    except Exception:                                       # noqa: BLE001
        ok_back = False
    if not ok_back:
        write_text(path, raw)
        return False, "删后回读校验失败，已还原原文件"
    return True, "已删除服务商 %s" % pid
