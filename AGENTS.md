# NovelForge — 项目操作手册（AGENTS.md）

本文件在 AI 助手于本项目目录工作时自动加载。用户在本项目发起任务时，按本手册执行。

## 项目定位

NovelForge（对外品牌名：**绒花墨坊** / `ronghuamofang`）是半自动长篇小说生成系统：机器包办苦力（素材归并→大纲→写作→检查→润色→Word），用户保留审批权和最终精修权。
用户只做两件事：**放置素材** + **说"运行 NovelForge 项目"**。其余由系统自动执行，关键节点设审批门暂停等待人工确认。

**执行引擎可切换**：`config/system.yaml` 的 `engine` 字段控制。`direct`=OpenAI 兼容直连（当前接 TokenHub，可随时换供应商），`hermes`=Hermes 子会话（备选）。所有调用点经 `make_client(cfg, role)` 取客户端，零调用点硬编码引擎。

**思考模式兼容（2026-09-14 起）**：glm-5.x / kimi 等模型默认进 thinking 模式时，正文会全进 `reasoning_content`、`content` 为空（且思考 token 吃掉 `max_tokens` 预算）→ 流水线产出空白。
防线有三：① `providers.<id>.disable_thinking_models` 名单内模型自动注入「关闭思考」片段（候选顺序见 `disable_thinking_payloads`，TokenHub 实测首选 `thinking: {type: disabled}`；`reasoning_effort: none` 会被该平台拒收）；② 未列名单的思考模型由客户端自动探测并注入后重试；③ 兜底**只取协议块**（`===FILE:` / `===APPEND:` / `===DELETE:`）：reasoning_content 里找不到
协议块就**直接丢弃**、返回空让上层判失败 —— **绝不把思考当正文写进产物**
（2026-09-23 修：原实现无标记时返回整段思考，写作阶段实测把思考残片写进了小说正文）。

**产出契约（2026-09-23 定）**：`_post_chat` / `_post_chat_stream` 返回 **4 元组**
`(text, usage, model, finish_reason)`。`finish_reason == "length"` 表示**输出被 max_tokens 截断、
正文不完整** —— 此时**不落盘**、返回 `exit_code: 2`、残缺文本隔离存 `data/state/truncated/`。
调用方无需改动（本项目统一以 `exit_code != 0` 判失败）。
**为什么必须这样**：思考模型会把输出预算耗在 reasoning 上（实测 `max_tokens=4000` 时
思考 10521 字、正文仅 466 字即被截断），旧代码不读 `finish_reason` → **半截章节会被静默当成品**。
注意 `completion_tokens_details.reasoning_tokens` **不可靠**（同样场景分别报 64 与 0），
思考成本只能从 `completion_tokens` 总额侧面反映。
**换供应商只改 `config/system.yaml` 的这两个键，不要改代码、不要在 `llm_client.py` 里硬编码模型名。**

## 启用流程（用户说"运行 NovelForge"时）

1. 前置检查：
   - `config/project.yaml` 是否已填书名/类型/章节数（书名仍为"示例书名（待填写）"时提醒用户）
   - `materials/raw/` 是否有素材（为空时提醒用户，不要空跑）
2. 启动：`terminal(command="python scripts/orchestrator.py", background=true, notify_on_complete=true, workdir="<repo>")`
   - 必须用项目 .venv 的 python：`<repo>/.venv/Scripts/python.exe`（POSIX 为 `<repo>/.venv/bin/python`）
   - 长任务（数小时），用后台运行 + 完成通知
3. 监控：轮询 `logs/runs.db` 与 `data/state/progress.json` 向用户汇报进度/成本
4. 退出码语义（orchestrator 返回）：
   - `0` = 全部完成；`1` = 阶段失败暂停；`2` = 预算熔断；`3` = 等待审批（阶段2 大纲 / 阶段6 润色）
5. 审批门：exit=3 时，提示用户审阅：
   - 阶段2 审阅 `data/outline/global.md`：审阅前先跑 `python scripts/outline_review.py` 看体检报告（标出空泛节点/章节规划缺漏），避免草草开工
   - 阶段6 审阅 `data/chapters/refined/`：润色稿满意再放行
   - 用户确认 → `python scripts/approve.py --stage N` → 重新运行 orchestrator（断点续跑）
   - 用户不满意 → 让用户说明意见：
     - 阶段2：跑 `python scripts/refine_outline.py "意见"`（增量修订，自动备份旧版到 `data/outline/history/`）→ 反复至满意 → 再审批
     - 其余阶段：跑 `python scripts/reject.py --stage N "原因"`（记录原因+清下游产物+重置状态）→ 重新运行 orchestrator（`--from N` 重跑）

## 止烧与安全闸门（2026-10-03 定，跑长任务前先看这一节）

**token 级熔断**：`config/system.yaml` 的 `budget.token_limit`（**engine: hermes 下唯一有效的止烧闸门**）。
- 为什么不是金额：hermes 走订阅流量，`estimate_cost_yuan` 对 `provider="hermes"` **恒返 0**
  → `budget.limit_yuan` 永不命中（虚设）。切回 `engine: direct` 时金额阈值自动恢复有效。
- 判据只有一份：`CostTracker.status_detail()`（金额 / token 累计 / token 单次），
  `status()` 只是它的二元组包装；触发后走**现有** budget/pause（`progress.budget.paused` + 退出码 2），
  没有新状态机。阶段内（stage4 逐章 / stage5 分批 / stage6 分卷）也查。
- 键义：`per_request_max_tokens`（direct 引擎真设进 `payload.max_tokens`；
  hermes **没有这个旋钮** → 只比对+告警，要按次熔断就开 `per_request_pause_hermes`）、
  `max_total_tokens`（本 run 累计输入+输出；**一轮 = 一次 orchestrator.run()**，`--from N` 重跑会新开 run_id → 计数归零）。
- 熔断后处置：调大对应键 → 清 `data/state/progress.json` 的 `budget.paused` → `orchestrator.py --from N` 续跑（已完成阶段不重跑）。
- 阈值参考量级：hermes 一次 stage1 子会话 ≈ 11.6 万 token（输入 9.8 万 + 输出 1.8 万）；40 章 stage4 合计通常 200~400 万。
- **阈值可配 + 出厂预设 + 边界校验**（2026-10-03）：默认 **1000 万/轮 + 单请求 50000**
  （用户口径：单章 5000~15000 字、常态 ~8000）。桌面端「**设置 → 预算与止烧**」可改，
  「恢复默认预设」取 `cost_tracker.TOKEN_LIMIT_PRESET`（**预设与出厂配置由用例断言逐项一致**）。
  越界值（0 / 负数 / 天文数字）**一律拒绝**并由 `validate_token_limit` 说清哪一项、区间是多少；
  手工把配置改坏时 `normalize_token_limit` 会**回落预设并打印 WARN** —— 闸门不许静默消失。
  端点：`GET/POST /config/token_limit`；写入口**只认 GUI 来源**并列入
  `agent_guard.FORBIDDEN_IN_AGENT_MODE`（外部 Agent 不得抬高自己的闸门）。
- **看得见**：`nfctl status` 打 `token 闸门: 已用 N / 上限 M（x%）（本 run #id）`；
  `GET /state` 的 `budget` 带 `tokens_used / token_limit / token_pct`；`estimate_tokens.py`
  给出「跑完预计 token / 是否撞闸门」；GUI 流水线条有同一项。
  ⚠️ 口径是**最近一次 run**（一轮 = 一次 `orchestrator.run()`；`--from N` 重跑 = 新 run，计数归零）。
- **未启用时会明说**「未启用（hermes 下金额阈值无效 → 本轮没有止烧闸门）」——
  静默不显示等于让用户以为有闸门。

**误开 `gates.auto_rewrite` = 双倍烧**（审稿前先跑一轮重写）。本机默认 `false`；
orchestrator 启动横幅会打印生效值（`gates: auto_rewrite=… auto_refine=… review_after_stage4=…`），别靠翻 YAML 猜。

**审稿 JSON 断裂**：解析统一走 `utils.validator.parse_llm_json`（含括号配对补全）；
仍失败 → 按 `gates.review_retries`（默认 1）重试 → 仍败则**原文隔离** `data/state/review_raw/` +
可行动报错 + 审稿门 fail-closed（不放行 stage5/6）。调用本身失败（退出码非零）**不重试**（同一上限只会再截断一次）。

**截断类失败不自动重试**（2026-10-03）：`finish_reason == "length"` 时除了隔离残缺稿，
还会写机器可读标记 `data/state/truncated/last.json`（判据 = `utils/truncation`，
写 `mark()` / 读 `after(阶段开始时刻)`）。orchestrator 的 `auto_retry`（默认开、默认 2 轮）
**先过 `should_auto_retry()`**：截断 → 一票否决并打印「同一上限只会再截断一次，白烧一倍输入 +
该调 `budget.token_limit.per_request_max_tokens`」。自检 `tests/unit/test_truncation_no_retry.py`（28 断言）。

**输入段格式**：任务「输入文件」段每条路径必须是**列表行**（`- 名称: <路径>`）。
格式不符（含目录引用指向不存在的目录）→ `InputSectionFormatError`（行号 + 原文 + 正确写法），
**不再静默返空** —— stage5 空稿事故的同类根因。

**Agent 模式守卫（CLI 侧）**：`gates.agent_mode=true` 时，`approve.py` / `reject.py` 会拒绝
**非人工来源**。人工证据 = **真实交互终端**：双端 `isatty` 只是前置快筛，主判据为
**祖先进程链取证**（链须落在用户终端锚点 explorer/cmd/powershell/bash/WindowsTerminal，
禁 node/python/pythonw/hermes*/winpty 等自动化宿主；取证失败或链截断 = fail-closed 拒）。
`--human` 与 `MOFANG_SOURCE=gui` 在 agent_mode 下**不构成人工证据**——只当声明留痕进
`agent_audit.jsonl`，不参与放行（2026-10-04 守卫加固 V1/V2）；拒绝文案零通道名，只说
「停手并报告用户」（V5，拒文本身不教绕行）。⚠️ 这不是沙箱，别对外宣称更强。

**控制台输出纪律**：`print` 里**不要**写非 GBK 字符（`¥`/`⚠️`/`✅`/emoji）——
stdout 被管道捕获时 Python 用 cp936，一行日志就能 `UnicodeEncodeError` 打死长跑
（实测：hermes 横幅把 orchestrator 启动搞崩、`quality_gate` 通过却退 1 假红）。
入口已统一调 `utils.console.ensure_utf8_stdout()` 兜底，但新代码请仍按此纪律写。

**project.yaml**：读写都必须走**带重复键检测**的 loader（`utils.project_config`）。
裸 `yaml.safe_load` 对重复键静默取后值 → 换书可能把数据归档到**错误书名**下。
`switch_book.py` 配置坏时**拒绝执行**（含 `--list`），改完再换书。
⚠️ 它的 `PROJECT_ROOT` 取自**脚本位置**（不是 CWD）：从别的目录敲
`python <某路径>/scripts/switch_book.py --archive`，动的是**脚本所在的那个仓库**
（本轮就因此归档过一次真实工作区，已恢复）。现它会在任何写操作前打印
「将操作的项目根」，CWD 不同还会显式提示 —— 别忽略那两行。

**配置类 YAML 一律经 `utils/config_io`（2026-10-03 收口）**：`load_config_yaml` 严格
（重复键/语法错 → `ConfigError` 带**行号 + 危害 + 体检入口**）、`load_pipeline_config`
（system ← local 深合并 + project，三份全严格）、`set_section_scalar(s)`
（**定向改写**保留注释 + 备份 + 写后按值回读；嵌套段路径如 `("budget","token_limit")`）。
**结构性护栏**在 `tests/unit/test_config_io.py` 第 5 节：`scripts/**` 出现裸 `yaml.safe_load(`
即 FAIL，豁免名单（模板 frontmatter / nfctl 只读诊断 / 设定集数据文件）**每条都要写理由**。

**收尾质量闸门（`quality_checklist.py`）**：**产物缺失 = 失败**（`all_pass=False` + 指出
`data/chapters/{refined,raw}/NN.md` 两个候选路径 + 说明「跑过但没落盘」的含义）。
确属还没写到那一章时用 `--allow-missing`，届时摘要必须写「**未经验收**」—— 跳过 ≠ 通过。
`book.chapters = 0` 这类空集合也直接报错（vacuous truth 是门禁最阴的假绿）。

## 命令速查（常用）

| 目的 | 命令（workdir=项目根，用 .venv python） |
|------|------|
| **只读全景 / 自检** | `python scripts/nfctl.py status` · `check` · `doctor` · `test` · **`release-check`（发版前一条命令跑全套）** · `api <GET路径>` |
| 跑流水线 | `python scripts/orchestrator.py`（`--from N` 续跑 · `--stage N` 只跑某阶段 · `--dry-run` 零 token 预演 · `--verbose` 落请求原文） |
| 审批 / 打回 | `python scripts/approve.py --stage N`（`--revoke` 撤销；阶段 1 审批 = 冻结 canon）· `python scripts/reject.py --stage N "原因"` |
| 换书 | `python scripts/switch_book.py --list` / `--archive` / `--restore "书名"`（均需 `--yes`；归档含 `config/` 与 `materials/`） |
| **副产物管理** | `python scripts/artifacts.py`（看清楚）· `--export <id\|all> --to <目录>`（导出）· `--clean <id,…> --yes`（清理，默认 dry-run） |
| **提交前门禁** | `python scripts/quality_gate.py`（未定义名/语法错零容忍）· `python scripts/leak_scan.py --list ci/blacklist.txt`（发布前/CI） |

> 其余 90+ 条命令（各阶段脚本、体检、精修、沙盒审核、成本、拆书、MCP 自检、各种自检用例…）
> 见 **`docs/commands.md`**。
>
> ⚠️ 为什么搬走（2026-10-03）：那张表 30 KB，把本手册顶到了 **64 KB 的指令加载预算**之上，
> 超限部分会被**静默截断** → 后面的《硬性约束》《能力边界》对自动加载的助手**不可见**，
> 等于手册失效且没有任何报错。现在本文件只留常用入口，完整表在 `docs/commands.md` 里照样可查。

## 外部 Agent 接入（Hermes skill + nfctl）

外部 Agent（Hermes / WorkBuddy）接本项目走**三条通道**：

1. **CLI（默认）**：项目脚本，不需要起服务。先跑 `python scripts/nfctl.py status`（全景）
   + `check`（自检），再按上表取具体命令。
2. **HTTP**（`nf_api` on `127.0.0.1:8765`）：只在需要「正在跑的那个任务」的状态、
   流式 token、SSE 时才用。只读转发用 `nfctl.py api <GET路径>`。
3. **MCP**：`nf_mcp.py` 在 **TCP 127.0.0.1:8766** 暴露白名单工具（`tools/list` 返回
   **25 个**：23 个 HTTP loopback + 2 个 LOCAL 直调 —— ⚠️ **数量勿写死**，以
   `nf_mcp.MCP_TOOLS` 为唯一事实来源），`tools/call` 经 HTTP loopback 复用
   现有域模块。审批类/项目类/模式切换端点**永不**暴露给 MCP。
   **垫片自启动（2026-10-06）**：`nf_mcp_stdio_bridge.py` 起来时若 8765 `/health` 不通
   会**代拉一次 nf_api**（连带起 8766），最多等 12s；逃生门 `NF_BRIDGE_NO_AUTOSTART=1`
   （测试/握手自检必设）。配置段在 GUI「设置 → 接入其他 Agent」按运行态生成；
   外部 Agent 启动提示词单一事实源 `prompts/agent_kickoff.md`（同页一键复制）。
   ⚠️ **8766 不是标准 MCP 传输**（是 TCP + 换行分帧的裸 JSON-RPC）——标准 MCP 客户端
   （Hermes / Claude Code / Cline）**必须经 stdio 垫片**接入：
   `scripts/nf_mcp_stdio_bridge.py`（stdio ↔ 8766 双向透传；`mcpServers` 配置示例见该文件头）。
   三层报错都带层号，别拿一层的错去修另一层的服务：
   **stdio 垫片 → 8766（nf_mcp 传输层）→ 8765（nf_api 服务本体）**。
   接入步骤 / 三级验证 / 排错对照表 → `docs/mcp-connect.md`；
   一条命令自检 → `python scripts/nf_mcp_handshake_check.py`（自起 18765/18766，不碰你在用的端口）。

**专属接线卡**：Hermes 技能库 `worldbuilding/ronghuamofang/`
（实机路径 `%LOCALAPPDATA%\hermes\skills\worldbuilding\ronghuamofang\`）
— `SKILL.md`（入口 / 自检 / 边界 / 处方 / 能力边界）、
`references/api.md`（97 个端点与请求体形状）。
与 `ai-novel-pipeline`、`novelforge-gui` 同分类同级；**技能库在 AppData、不进 Git，改动前先留副本**。

**Agent 模式守卫**：`gates.agent_mode = true` 时，非 GUI 来源（无 `X-Mofang-Source: gui`）
调用 `/approve`、`/reject`、`/project/create|archive|restore|init`、`/config/agent_mode`、
**`/config/token_limit`**（止烧阈值：Agent 不得抬高自己的闸门）
返回 **403**。含义是：**外部 Agent 可读、可跑流水线，但不能代替用户审批** —— 这是设计，不是 bug。
另外设置类写入（`/config/agent_mode`、`/config/token_limit`）**无论模式如何都要求 GUI 来源头**。

## 提示词前缀纪律（2026-09-23 定，改 `prompts/stage*.md` 前必读）

模型的**前缀缓存**按「请求开头逐字相同」命中，而写作阶段的输入量远大于输出量，
所以**提示词里内容的先后顺序直接决定成本**。

**约束**：`prompts/stage4_writing.md` 的 body 中，`---` 分隔线**之前**的内容必须逐章逐字不变；
凡含 `{{n}}`（章节号）或每次都会变的内容（前文衔接 / 风格样本 / 角色卡），一律排在分隔线之后。

**依据（实测）**：原模板正文第 1 行就是 `# 阶段 4 任务：写作第 {{n}} 章`，分叉点落在第 15 个字符，
其后 55 行的写作要求/文风要求/硬性禁止**全部落在缓存分叉点之后**，前缀命中率上限只有 1.2%；
按本条重排后为 **77.6%**，真请求实测 deepseek-v4-flash 命中 57.9%。

**说明文字写 frontmatter，不要写进 body** —— frontmatter 不进任务文本（`load_template` 只取 body），
写进 body 会变成逐章变化的内容，反而破坏前缀（这个坑已踩过一次）。

**frontmatter 里没有任何键会被代码当参数用**（2026-10-06 实证：20 个 `load_template()`
调用点无一读取 `meta`；模型/温度/上限的真源是 `config/system.yaml` 的 `model.*` 按角色取）。
原先 15 个模板里的 `model: hy3` 因此被判死字段并从**三份副本**（源码树 / 桌面工作区 /
安装包 payload）一并清除 —— 它的危害在**误导**：GUI 提示词编辑器显示它、「复制提示词」
把它连正文一起塞进剪贴板，读的人（和外部 Agent）会以为模板在挑模型。
复制按钮现已剥掉 frontmatter 只给正文；防回潮见 `tests/unit/test_prompt_frontmatter.py`。

**另注**：`qwen3.5-flash` 在 TokenHub 上**完全不缓存**（同前缀连发 3 次 cached 恒为 0），
而当前 7 个角色（含 writer）全用它 —— 即上述优化要变现需换到支持缓存的模型（见 `cost_tracker.RATES` 的 cache_read）。

## 架构：API 层的域模块拆分（P2）

`scripts/nf_api.py` 原本是 2,872 行的 God Object（91 个端点全挤在
`do_GET`/`do_POST` 两条 elif 链里）。现已拆为 **2374 行的分发器 + 8 个域模块**，
端点实现住在 `scripts/nf_api_domains/`。

**改 API 时请遵守三条纪律**（全部由 `tests/unit/test_api_domains.py` 守护）：

1. **谁发响应只能有一个答案** —— 域模块的 `handle_*(h, ...)` 只
   `return (status, payload)`，**绝不**调用 `h._send()` / `h._stream_sse()`。
   `nf_api.py` 的分支体统一写 `self._send(*_dom(dom_x.handle_y(self)))`。
2. **反向依赖走属性访问** —— 域模块要取 `nf_api` 的模块级名字时写
   `import nf_api as api` 然后 `api.ROOT`、`api.JOBS`；**禁止**
   `from nf_api import ROOT` —— 后者把 `ROOT` 拷成**死值**，
   让 `--root` 参数与测试的临时项目根全部失效。
3. **分发器留在 `nf_api.py`** —— `do_GET`/`do_POST` 的 elif 链是契约测试的
   路由表锚点，不能搬走；只搬分支体。

**加新端点**：实现写进对应的域模块并加 `ROUTES` 条目，`nf_api.py` 里只加一行转发。
某类端点超过 3 个就另开一个域模块，不要堆回 elif 链。

**当前域模块清单**：

| 模块 | 职责 | 端点数 |
|------|------|--------|
| `project.py` | 健康/状态/项目列表/配置读取/Agent 模式 | 8 |
| `runtime.py` | 后台任务/审查/校对/模板/预估/节奏（只读） | 7 |
| `models.py` | 模型列表/切换/添加/白名单校验 | 6 |
| `outline.py` | 大纲结构/历史/差分/drafts/趋势/建议/沙盒队列 | 10 |
| `materials.py` | 素材/碎片读写 | 5 |
| `misc.py` | 章节质量/验证/日志/关于/成本/批量进度 | 6 |
| `post_misc.py` | Markdown 导出 | 1 |
| `sandbox.py` | 沙盒审核/文件预览 | 2 |
| `refine.py` | 大纲精修/节点精修/撤销/章节精修 | 4 |

### Agent 模式（外部 Agent 控制，P1 新增）

绒花墨坊支持双模式运行：

- **标准模式（默认）**：GUI 内手动操作，HTTP API 仅本地 GUI 消费
- **Agent 模式**：外部 Agent（如 Hermes）可通过 HTTP API 调用绒花墨坊进行大纲草拟、设定起草等操作

**开关**：`config/system.yaml` 的 `gates.agent_mode`（默认 `false`）

**端点**：
- `GET /config/agent_mode` —— 读取当前开关状态
- `POST /config/agent_mode` —— 设置开关（仅 GUI 手动切换，Agent 不得调用此端点开启）

**安全边界**：
- Agent 模式下，以下操作**仍只允许 GUI 内手动执行**（API 返回 403）：
  - `/approve`（审批）、`/reject`（打回）
  - `/project/create`、`/project/archive`、`/project/restore`
  - `/project/init`
  - `/config/agent_mode`（Agent 不得自行切换模式）
  - `/config/token_limit`（止烧阈值 —— Agent 不得抬高自己的闸门）
  - `/project/archive/delete`（删除归档 = 抹掉成书数据；端点另要求 `X-Mofang-Source: gui`）
- Agent 生成的大纲/产物自动进入待审批状态（exit=3），需在桌面端确认
- 双保险机制：Agent 被告知需在桌面端审批 + 桌面端自动检测审批门并弹窗提示
- 审计日志：所有 Agent 调用记录到 `data/state/agent_audit.jsonl`

**前端配合**：
- 「设置」页新增「Agent 模式」toggle（二次确认）
- 顶部连接状态旁显示 `⚡ Agent` 徽标（橙色脉冲动画）
- 全局刷新按钮（↻）一键刷新所有页签数据
- 审批门产物摘要显示 `agent: true/false` 标记

### Agent 可调用端点清单

**✅ 可调用**（查询类）：
- `GET /state`、`/health`、`/about`
- `GET /models`、`/models/available`、`/models/cache`、`/models/fetched`
- `GET /config/project`、`/config/style_notes`、`/config/agent_mode`
- `GET /outline/structure`、`/outline/trend`、`/outline/advise`、`/outline/history`、`/outline/diff`
- `GET /sandbox/queue`、`/sandbox/file`
- `GET /review/report`、`/review/decisions`
- `GET /chapters/quality`、`/chapters/history`
- `GET /costs`、`/costs/summary`、`/costs/streaming`
- `GET /estimate`
- `GET /prompts/list`、`/prompts/get`
- `GET /project/list`、`/project/status`、`/project/archive/tree`、`/project/archive/file`
  （归档**只读**查看：清单 + 单文件预览，越界/超大/二进制有闸）

**✅ 可调用**（写入沙盒/草稿类，产物自动进入待审）：
- `POST /outline/save`、`POST /outline/chapters/save`
- `POST /refine/outline`、`POST /refine/chapter`
- `POST /stage/{n}/run`（生成大纲/章节）
- `POST /auto_rewrite/run`
- `POST /sandbox/review`（驳回/退回，但需在 GUI 中确认）

**🚫 禁止调用**（返回 403）：
- `POST /approve`、`/reject`
- `POST /project/create`、`/project/archive`、`/project/restore`、`/project/init`
- `POST /project/archive/delete`（删归档，默认进回收站；GUI 来源头 + 逐字书名确认双闸）
- `POST /config/agent_mode`（Agent 不得自行切换模式）

### ⚠️ 路径 IO 必须经 `ROOT`，不得用相对路径

API 层**禁止**写 `Path("data/...")` 这类相对路径 —— 它隐式依赖进程 CWD，
而同族端点用 `ROOT / ...`，两种语义并存会在 `--root` 场景下**静默读错项目**：

```
cwd = 书本A（有 review_report.json）    --root 书本B（没有）
→ GET /review/report 返回 200，内容是书本A 的数据   ← 不报错，给错数据
```

生产（Electron）恰好没暴露它，因为主进程 `spawn(..., {cwd: ws})` 且同时
传 `--root ws`，两者相等。但 `--root` 参数的存在本身说明设计允许它们不同。

统一写 `ROOT / "data" / "outline" / "x.json"`（域模块里写
`api.ROOT / ...`）。由 `tests/unit/test_api_domains.py` 第 7 节守护。

### ⚠️ `__main__` 别名守卫（勿删）

`nf_api.py` 导入区有一段：

```python
if __name__ == "__main__":
    sys.modules.setdefault("nf_api", sys.modules["__main__"])
```

**这不是可选优化。** 以 `python scripts/nf_api.py` 启动时本文件模块名是
`__main__`；域模块里的 `import nf_api as api` 会**再加载一份**，于是进程内有两个
`nf_api`，`JOBS` / `CURRENT` / `ROOT` 各自独立。后果是**静默**的：
从磁盘读的端点（`/state`、`/costs/…`）一切正常，只有依赖进程内状态的
`/jobs/{id}` 恒 404 → 前端轮询后台任务全部超时。更阴的是纯单测**全绿**
（只有一份模块），缺陷只在真机 HTTP 下暴露。删掉它 → 域护栏 4 条 FAIL。

## 关键路径

- 设定集：`data/setting/setting.json`（四顶层键 characters/world/plot_fragments/timeline）
- 整体大纲：`data/outline/global.md`（审批门对象）
- 大纲体检报告：`data/outline/review_report.md`（outline_review.py 生成）
- 素材体检报告：`data/setting/material_review.md`（material_review.py 生成，stage1 后自动）
- 设定补全历史：`data/setting/history/`（setting_refine.py 每次补全自动备份 setting_vN.json）
- 角色出场统计：`data/state/appearances.json`（appearances.py / `refresh_appearances()` 生成，**stage4 每章通过校验后自动刷新**，obsidian_postprocess 自动调用；同章重复同步不重复计数）
- 风格偏差报告：追加在 `data/outline/polish_report.md`（分卷为 `polish_report_volN.md`）末尾，
  stage6 每卷润色后自动写入；配置 `book.style_reference` 才生成，阈值 30%，仅供参考不阻断流程
- 大纲修订历史：`data/outline/history/`（每次精修自动备份 global_vN.md，可回退）
- 用户大纲输入：`config/project.yaml` 的 `book.user_outline`（可选；提供后 stage2/精修优先遵循）
- Word 成品模板：`templates/*.dotx`（config 的 `book.word_template` 指定；.dotx 自动转换；替换 [书籍标题]/[作者]/[目录占位符]/[请输入文本] 占位符；更换模板只改配置或覆盖 templates/）
- 项目快照：`history/{时间戳}_{标签}/`（每阶段成功后自动生成，保留最近 10 份；data/ 不进 git，快照承担版本职责）
  - **恢复**：`python scripts/snapshot.py --restore <ID> --yes`。默认 dry-run；
    恢复前自动存 `pre_restore` 快照作折返点；快照外的文件默认保留。
- 多书归档：`data/books/{书名}/`（switch_book.py 归档/恢复；切换前 orchestrator 会提示书名不一致）
  - **查看 / 删除**：`GET /project/archive/tree|file`（只读）、`POST /project/archive/delete`
    （GUI 来源头 + 逐字书名确认；默认移入下条的回收站，`purge=true` 才真删）
  - 归档回收站：`data/books/_trash/{书名}__{时间戳}/`（删除的默认落点，要彻底清除手工删该目录；
    `list_books()` 跳过它，不会以「一本叫 _trash 的书」出现在列表里）
- 章节修订历史：`data/chapters/history/`（refine_chapter.py 每次精修备份 chNN_vM.md）
- 校对报告：`data/outline/proofread_report.json` + `.md`（proofread.py / `POST /proofread/run` 产出；
  **schema 与 review_report 对齐**：finding 带 `id/type/severity/detail/suggested_action`，
  故审稿 UI 与 batch_refine 决策链路可直接复用。`rhythm` 字段含逐章节奏与离群判定）
- 拆书节奏：`data/state/book_pacing.json`（book_split.py 产出；`GET /book/pacing` 读它，
  `GET /book/pacing?source=current` 则实时算本书、不落盘）
- 拆书切分正文：`data/state/book_split/<书名>/NN_标题.md`（仅 `--emit` / `{"emit":true}` 时生成）
- 生成前预估：`GET /estimate`（无落盘；GUI 运行前确认框用它，**dry-run 性质，不产生副作用**）
- 自动重写报告：`data/outline/auto_rewrite_report.md`（人可读，每次运行追加）
  / `data/outline/auto_rewrite_report.json`（机器可读，`latest` + `runs` 最近 50 次）
- 自动重写状态：`data/state/progress.json` → `stages["4"].auto_rewritten`（`{章号: 已用轮次}`，
  幂等依据；轮次用尽后保留 `needs_rewrite` 转人工）。开关 `gates.auto_rewrite`（默认 false）
  / `gates.auto_rewrite_max_rounds`（默认 1）
- **长跑保护**（2026-10-01）：`gates.stage4_max_consecutive_failures`（默认 3，0=关）——
  stage4 **连续** N 章失败即判定系统性问题（模型挂了/提示词改坏/输入构造错）并**提前停止**，
  避免"坏掉了还一路跑完 100 章"在 `data/chapters/raw/` 铺满废稿；已完成的章节不受影响。
  另：stage4/5/6 现在**在阶段内部**也查预算熔断（此前只在阶段之间查 —— 而一章就能烧掉几元，
  限额对"一次跑几十章"这种最该保护的情形恰好失效）
- 控制台触发：`POST /auto_rewrite/run {threshold?, max_rounds?, chapters?, dry_run?}`
  （**dry_run 默认 true**，须显式 `dry_run:false` 才真正改稿；`GET /state` 可只读查看上述状态）
- 设定库引用索引：`materials/vault_links.md`（stage1 归并后自动生成，素材→设定条目溯源）
- 原始创作碎片：`materials/original_scraps/`（**用户私人数据，自由命名、不进 Git**；与 `materials/raw/`
  分工：raw=管线消费的结构化卡片，original_scraps=人类乱写的原始碎片，只在 stage1 归并时作为额外输入）
- 碎片聚类索引：`data/setting/scraps_index.json`（scrap_cluster.py 生成；聚类结果 + 时间轴 + 前瞻备忘，
  内容一并进 stage1 素材指纹，改碎片会触发设定集重新归并）
- 碎片归并产物：`data/setting/scraps_merge.json`（stage1 `--scraps` 时由 LLM 产出，信息点/冲突/待确认项）
- 碎片→卡片备份：`materials/original_scraps/_backup/`（碎片改写前）、`materials/raw/_backup/`（同名卡片覆盖前）
- 风格参考：`config/project.yaml` 的 `book.style_reference`（可选，写作阶段注入范文）
  - 配置后 stage4/stage6 额外注入**范文片段（few-shot）**：`style_analyzer.extract_style_samples()`
    从范文抽取 ≤3 段代表性原文，按空行分段、按"长度适中/含对白/含修辞/节奏有起伏"打分，
    自动剔除与当前内容字面重叠（3-gram 重叠率 > 0.30）的段落，并标注来源（第 N 段，字符 X-Y）
  - 未配置 `style_reference` 时不注入任何片段与风格指令，逻辑与旧版一致
- 用户风格笔记：`config/project.yaml` 的 `book.style_notes`（可选，手动补充的风格要求）
  - 与自动分析结论叠加注入 stage4/stage6，冲突时以笔记为准；留空则完全不注入
  - GUI 编辑：控制台「设置」页签「用户风格笔记」→ IPC（`style-notes:get/save`）→
    nf_api `/config/style_notes` → `utils/project_config.set_style_notes()`
    （按行定向改写 project.yaml 保留注释，写入前备份 `config/history/`，写后回读校验）
- 提示词模板备份：`prompts/history/{模板名}_{时间戳}.md`（GUI 保存前自动备份，每模板留最近 50 份）
- 逐章大纲：`data/outline/chapters/NN.md`
- 章节：`data/chapters/raw`（原稿）/ `checked`（检查后）/ `refined`（润色后）
- 滚动摘要：`data/summaries/rolling.md`（全书摘要 + 近 5 章）
- 进度：`data/state/progress.json`；运行记录：`logs/runs.db`
- 最终成品：`output/{书名}_完整版.docx`

## 运维场景

- **阶段失败（exit=1）**：读 orchestrator 输出定位失败阶段 → 检查原因（校验失败/子会话异常）→ 修 `prompts/` 模板或素材 → 重跑 `--from N`
- **熔断暂停（exit=2）**：先看 orchestrator 打出的 `熔断暂停[原因]`——
  `原因=tokens_total` → 调 `config/system.yaml` 的 `budget.token_limit.max_total_tokens`；
  `原因=yuan` → 查 `SELECT SUM(cost_yuan) FROM cost_log` 并与用户确认是否调高 `budget.limit_yuan`
  （**engine: hermes 下金额恒 0，不可能命中**）。改完清 `data/state/progress.json` 的
  `budget.paused` → `orchestrator.py --from N` 续跑（已完成阶段不重跑）。
- **用户打回**：`python scripts/reject.py --stage N "原因"`（记录原因、清理 N 及下游产物、重置状态、撤销审批；history/ 备份保留可回退）→ `--from N` 重跑。打回 2 用精修通道（refine_outline）而非 reject
- **GUI 排障（2026-10-06 对齐方寸）**：顶栏「📋 日志」常驻；设置 → 诊断与调试
  （运行形态/健康态/代码根/数据根/日志路径 + 查看日志/打开日志文件/复制诊断信息/
  DevTools 开关（Ctrl+Shift+I 或 F12）/重启后端 API；重启在流水线运行中禁用）。
- **素材更新**：新增素材后 `--from 1` 重跑（已有章节文件自动跳过，不会重写）
- **低分章自动重写（方向3）**：默认关闭。开启：`config/system.yaml` 的 `gates.auto_rewrite: true`
  → stage4 后自动重写 `quality < chapter.quality_threshold` 的章节（排在审稿分支之前）。
  手动跑：`python scripts/auto_rewrite.py`（先 `--dry-run` 预演）。批次报告见
  `data/outline/auto_rewrite_report.md`；重写后仍不达标的章保留 `needs_rewrite` 转人工，
  用 `batch_refine.py` 处理。**开启前须与用户确认**（会产生 LLM 费用）
- **设定自动补全（auto-thin 闭环）**：默认关闭。开启：`config/system.yaml` 的
  `gates.setting_refine_auto: true` → stage1 归并+体检后自动跑一轮定向补全
  （点名 THIN 角色 + 各自缺失维度 → 子会话补全 → 复评）。
  三条止损：**达标即停**（THIN=0 零调用短路）/ **无进展即停**（THIN 未降）/
  **轮次上限**（`gates.setting_refine_max_rounds`，默认 1，建议 ≤2）。
  手动跑：`python scripts/setting_refine.py --auto-thin --dry-run` 先预演。
  ⚠️ 钩子在 orchestrator 里被 `try/except` 包裹（失败不阻断流程）—— 若它失效
  只会打印「失败（不影响流程）」，故 `test_setting_refine_auto.py` 用
  `inspect.signature().bind()` 守签名，防止静默降级
- **提示词调优**：直接改 `prompts/stageN_*.md`（模板与代码分离，无需改脚本）

## 能力边界（2026-09-21 实证，勿重复夸大）

**本系统是「确定性流水线 + 单轮 LLM 调用」，没有 agent 层。** `engine: direct` 是
OpenAI 兼容直连，模型**无工具调用、无自主多轮循环、无记忆**。「多轮迭代」全部靠
外层（用户或外部 agent）驱动。以下是逐项实证的能力边界：

| 能力 | 现状 | 缺口 |
|---|---|---|
| 大纲多轮迭代 | ✅ 每轮备份 `global_vN.md` 可回溯；体检零 token；**✅ 收敛判断**（`--trend`：done/improving/stalled/mixed + 建议）；**✅ 迭代成本进账本**（`stage=2` + `chapter=版本号`，`cost_report --by-outline` 逐轮可见） | — |
| 调设定库 | ⚠️ 能导入能注入 | ❌ 整体替换非合并 ❌ 无增量同步 ❌ 大纲阶段不注入 vault 词条（只给 `setting.json` 路径）。**注：冲突消解靠机器规则解决不了（哪个设定更对是创作决策）—— 若要合并，需要「把冲突摊出来让用户当场决断」的交互，而非自动规则** |
| 反向写沙盒等审核 | ✅ **审核状态机**（`pending/approved/rejected` + 独立状态库 + 审核队列 CLI + 孤儿检测）；✅ **大纲迭代阶段可回写**（`outline_export.py` 导出审核包，只写沙盒不改产物）；✅ **GUI「审核」页签**（队列 / 预览 / 通过 / 驳回 / 退回，徽标提示待审数） | ❌ 完书后通道未接审核状态机；❌ 无「一键粘贴进 vault」——**刻意不做**（见下） |
| 迭代后给开工方向 | ✅ **`outline_advisor.py`**按严重度给候选方案（结构问题 > 退化 > 有 THIN > 停滞 > 开工），每个方案含依据 + 目标条目 + **可执行 next_step** + 调用次数 + 取舍；✅ **GUI 大纲页签「趋势 / 建议」抽屉**（趋势表 + 候选方案 + 逐条「AI 精修此条」一键提交） | ❌ 只给方案不自动执行（刻意：`start_writing` 是用户的决定，不是系统的）；❌ 结构性问题（如四节缺失）无自动改法，如实说「需手工处理」 |

**回写设定库的硬边界（2026-09-21 用户确认）**：任何回写设定库（Obsidian vault
或后续新增支持的其它设定库）的动作，**一律只写沙盒目录产出草稿样本，
永不直接覆盖 vault / 正典内容**。决策权始终在用户手里 —— 系统负责「把候选摊出来
+ 标清来源与状态」，用户负责「选哪个、什么时候入库」。基于此：

- 沙盒是**唯一可写位置**；vault 本体只读（`obsidian_bridge` 的写入函数全部落沙盒）。
- 审核状态（`pending/approved/rejected`）存在独立的 `data/state/sandbox_manifest.json`，
  **不写进产物 frontmatter** —— 否则用户粘贴时会把内部状态一起带进 vault 词条。
- 内容变了（sha256 比对）自动重置为 `pending`：**绝不让旧审核为新内容背书**。
- 系统**不提供**「通过后自动写入 vault」的通道。也不应新增 —— 那等于绕过用户决策。

**收敛判断的口径**（确定性，不是 LLM 判断）：主键 `issues + thin`（越小越好），
看最近 `window`（默认 3）轮 —— 最新为 0 = `done`（可开工）；持续下降 = `improving`；
完全不变 = `stalled`（收益递减，建议换角度/补素材/开工）；有升有降 = `mixed`（建议回看哪几条在反复）。

**结论**：绒花墨坊负责「手」（确定性地读写文件、调 LLM、算成本、留备份、做体检），
**「脑」需要外层 agent**（读体检 → 判断收敛 → 给方向 → 调 refine → 审沙盒产物）。
两者是互补而非替代关系。

## 硬性约束

- **Obsidian 设定库只读**：永不写入 vault；系统仅通过 `materials/` 与项目内 `data/` 工作
- `data/`、`history/` 不进 Git（由 history/ 快照承担版本职责）；`prompts/`、`config/`、`scripts/` 进 Git
- **用户私人数据不进 Git**：`materials/original_scraps/`（及 `_backup/`）永不随源码仓库走，后续由独立的
  导入/导出功能承担迁移；碎片→卡片落盘一律由 Python 执行（模型写入白名单只有 `data/**`），且覆盖前先备份
- **模型凭据**：项目 `.env`（不入 git）承载 LLM API Key（`TOKENHUB_API_KEY`）；engine=hermes 时凭据由 Hermes 统一管理。切换引擎/模型/服务商只改 `config/system.yaml`（`engine` / `model.*` / `providers`），新 provider 计价须先补 `scripts/utils/cost_tracker.py` 的 `RATES`（可用 `scripts/price_wizard.py` 交互式更新）。**模型白名单纪律：用户免费体验包按模型领取，未经用户确认不得指定/更换付费模型**
- **直连引擎（engine: direct）语义**：无子会话工具循环——任务文件的输入文件段由 `llm_client.inline_inputs` 全文内联进单请求；多文件输出任务自动按目标文件拆分请求；模型产物经 `===FILE/APPEND/DELETE===` 协议落盘（白名单：`data/**` 与 `logs/runs.db`），期望外路径直接拒绝
- **子会话（engine: hermes）**：任务文件（data/state/tasks/）必须自包含全部上下文
- **本地 API（nf_api.py）**：函数级复用 orchestrator/approve/reject/refine，不经过 Hermes 子进程；客户端注入走 `_client_for_env`（默认 make_client 真引擎，`NF_API_ALLOW_FAKE=1`/`--allow-fake` 时注入 FakeClient——**仅限测试**，自测脚本运行会清空 data/ 运行产物）；服务默认只绑 127.0.0.1
- **GUI 交互纪律**：① 运行入口唯一（流水线页签顶部的「一键工作流」条，旧的 `.run-all-bar` 已删——
  新增运行按钮一律并入该条，不让两套入口并存）；② 破坏性操作走应用内确认框 + 勾选护栏，不用
  `window.confirm`（Electron 原生对话框不可靠）；③ 新 CSS 一律用主题变量（`--ok/--bad/--warn/--accent`），
  硬编码色值在暗色主题下会看不清；④ 新增/改前端后必须跑 `tests/e2e/e2e_ux_verify.py` 做真机视觉验收
  （用户明确要求：不许只跑冒烟测试就交付）；⑤ 前端 API 基址可用 `window.__NF_API_BASE__` 覆盖，
  便于在不干扰用户 8765 实例的前提下验收
- **新建项目只有一个入口**：「项目」页签 →「＋ 新建项目」（`POST /project/create`：快照 → 归档当前 →
  建空工作区 → 写 project.yaml）。手工改 `config/project.yaml` 只算高级用法，不要写进引导文案；
  首启向导（`POST /project/init`）走同一实现，**必须真写盘**（历史上的静默丢弃已修）
- **project.yaml 只用定向改写**：一律经 `utils/project_config.set_book_fields()`（按行替换 + 备份 +
  写后回读校验），**不要整份 dump**，否则注释与排版全丢。多行值用块标量 `|-`（用 `|` 会多带一个换行，
  导致回读不一致）
- 破坏性操作前必须先 `snapshot.py` + 征求用户确认（删除章节产物、删除原始碎片、覆盖写已存在的素材卡）
