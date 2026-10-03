# 把绒花墨坊接进标准 MCP 客户端（Hermes / Claude Code / Cline）

> 面向：想让外部 MCP 客户端读绒花墨坊状态、跑流水线的用户。
> 对象版本：v0.3.2+（`scripts/nf_mcp_stdio_bridge.py` 存在即为已含垫片）。

## 1. 先弄清三层，再动手

```
标准 MCP 客户端（Hermes / Claude Code / Cline / Cursor）
   │  stdio：换行分隔 JSON-RPC 2.0        ← 客户端只会这一种（spawn 进程说 stdio）
   ▼
scripts/nf_mcp_stdio_bridge.py            ← ① stdio 垫片
   │  TCP 127.0.0.1:8766
   ▼
scripts/nf_mcp.py                         ← ② 8766 MCP 传输层（25 个 nf_* 白名单工具）
   │  HTTP 127.0.0.1:8765
   ▼
scripts/nf_api.py                         ← ③ 8765 服务本体
```

**为什么必须有垫片**：8766 是「TCP + 换行分帧的裸 JSON-RPC」，**不是** MCP 标准传输。
标准客户端只做「spawn 一个进程 + 用 stdin/stdout 说话」，所以直连 8766 一定失败。
垫片干的就是这一层翻译（两侧分帧格式一致，实质是逐行透传）。

任何一层不可用，报错都会**写明是哪一层** —— 别拿一层的错去修另一层的服务。

## 2. 三步接入

### 步骤 1：确认两件事在跑

```bash
# 用控制台启动时（推荐）：控制台会自动拉起 8765 与 8766
# 手动启动：
.venv/Scripts/python.exe scripts/nf_api.py      # 8765 服务本体
.venv/Scripts/python.exe scripts/nf_mcp.py      # 8766 MCP 传输层
```

自检：<http://127.0.0.1:8765/health> 能返回 JSON。

### 步骤 2：在客户端配置里加一条 stdio server

以 **Hermes** 为例，配置文件在 `%LOCALAPPDATA%\hermes\config.yaml` 的 `mcp_servers:` 段
（同段内已有 stdio 型条目可参照，形如 `command:` + `args:` + `enabled:`）：

```yaml
  novelforge:
    command: <项目根>\.venv\Scripts\python.exe      # 必须用项目 venv 的 python
    args:
      - <项目根>\scripts\nf_mcp_stdio_bridge.py
    enabled: true
```

Claude Code / Cline / Cursor 的配置形状相同（都是 `mcpServers` 下的 `command` + `args`）：

```json
{
  "mcpServers": {
    "novelforge": {
      "command": "<项目根>/.venv/Scripts/python.exe",
      "args": ["<项目根>/scripts/nf_mcp_stdio_bridge.py"]
    }
  }
}
```

⚠️ **改配置前先备份**（`config.yaml` → `config.yaml.bak`），改完需要让客户端重新加载
（重启客户端或触发它的 MCP 重载）。

### 步骤 3：验证（三级，从便宜到彻底）

**① 离线自检**（不起任何服务，验证垫片自身行为）：

```bash
.venv/Scripts/python.exe tests/unit/test_mcp_stdio_bridge.py     # 15 断言，全离线
```

**② 真机握手**（一条命令跑完：真 nf_api + 真 nf_mcp + 官方 SDK 客户端）：

```bash
.venv/Scripts/python.exe scripts/nf_mcp_handshake_check.py
```

它会自己起 18765/18766（**不占你正在用的 8765/8766**），用**官方 MCP SDK**
（`mcp` 包，不是自造协议栈）完成 `initialize` → `tools/list` → `tools/call`，跑完自动清理。
期望结果（实测基线）：

```
[PASS] 8766 MCP 传输层已就绪（真 nf_mcp）
[PASS] 标准客户端 initialize 握手成功（垫片 → 8766）  → name='novelforge-mcp' version='0.1.0'
[PASS] 协议版本已协商  → 2024-11-05
[PASS] tools/list 返回工具清单  → 25
[PASS] 工具全为 nf_* 前缀（白名单未被污染）
[PASS] 工具数量与 MCP_TOOLS 声明一致  → 25 个（声明 25）
[PASS] tools/call 真调用成功（全链路 垫片→8766→8765 通）  → book=<你的书名>
---
通过 7 / 失败 0 / 跳过 0
```

缺 `mcp` SDK 时它会打印安装指引并**干净跳过**（exit 0）—— 按项目纪律，缺依赖是「未执行」，
不等于「失败」。

**③ 客户端实测**（最终判据）：在 Hermes / Claude Code 里问一句
「列出 novelforge 的工具」，应当看到 25 个 `nf_*` 工具；再调一个只读的
（如 `nf_get_state`）确认能拿到真实书档数据。

更省事的做法：`python -m mcp.client <项目根>/.venv/Scripts/python.exe <项目根>/scripts/nf_mcp_stdio_bridge.py`
—— 这是 **MCP 官方 SDK 自带的 CLI 客户端**，能完成一次标准握手（需要装 `mcp` 包）。

## 3. 排错对照表

| 症状 | 坏在哪一层 | 处置 |
|---|---|---|
| 报 `-32002` + 「无法连接 8766 MCP 传输层」 | **①→②**：8766 没起 | 起 `scripts/nf_mcp.py`，或确认控制台已拉起 MCP 层 |
| 报 `8765 服务本体（nf_api）` 不可达 | **②→③**：8766 在、8765 不在 | 起 `scripts/nf_api.py`；确认 `/health` 可访问 |
| 报 `8765 服务本体` 且带 `HTTP 502/503/504` | **②→③**：企业代理/沙箱把「连不上」表现成网关 5xx | 同上（这是**等价情形**，不是"网关坏了"） |
| 工具列表是空的 | 客户端没拿到 `tools/list` | 先跑步骤 3 的 ②，区分是客户端问题还是服务端问题 |
| 客户端根本不出现在「已连接」列表 | **配置层** | 确认 `command` 指向**项目 venv 的 python**（不是系统 python）、`args` 路径正确、`enabled: true`，并让客户端重新加载配置 |
| 请求发出去没响应、客户端挂住 | **垫片 stdout 被污染** | 垫片 stdout 只许走协议；若有人往它 stdout 打日志，客户端会解析失败 |

## 4. 边界（MCP 侧**故意**不开放的能力）

- **审批类**（`/approve`、`/reject`）、**项目类**（`/project/create|archive|restore|init`）、
  **模式切换**（`/config/agent_mode`）：外部 Agent 可读、可跑流水线，**不能代替用户审批**。
- 工具白名单共 **25 个**，`nf_mcp.py` 的 `MCP_TOOLS` 是**唯一事实来源**。
  ⚠️ **数量断言一律不写死**：`nf_mcp_handshake_check.py` / `test_mcp_e2e` /
  `test_agent_dispatch` / `test_mcp_remedy_snapshot` 都与 `len(MCP_TOOLS)` **动态比对**
  —— 新增工具只需改 `MCP_TOOLS` 一处（写死数字每加一个工具就假红一次，假红会被当噪声）。
