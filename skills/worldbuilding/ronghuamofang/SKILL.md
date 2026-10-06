---
name: ronghuamofang
description: "绒花墨坊（NovelForge）的操作接线卡：让 Hermes 主动查询与驱动这本书的流水线。需要看写到哪了、跑/续跑阶段、体检大纲与设定、审稿闭环、看成本、导出 Word、切书档时加载。触发词：绒花墨坊、NovelForge、跑书、流水线、大纲体检、章节进度、成本、审批门、nf_api、nfctl。区别于开发类 skill（novelforge-gui / ai-novel-pipeline）—— 本卡只管「用」，不管「改」。"
version: 1.1.0
metadata:
  hermes:
    tags: [novelforge, 绒花墨坊, novel, pipeline, 操作卡, nfctl, nf_api, 审批门]
    category: worldbuilding
    related_skills: [ai-novel-pipeline, novelforge-gui]
---

# 绒花墨坊 ↔ Hermes 操作卡

接线卡模式：只写**入口 + 自检 + 边界 + 处方**。命令与端点的**唯一真源是现场读**
`AGENTS.md`、`scripts/nf_api.py`（头部 docstring 就是端点总览）、`scripts/*.py --help` ——
本卡不维护会过期的清单快照。

项目根 `$NF` = **本仓库克隆根**（本卡就在仓库里，路径由克隆位置解析，**不把本机绝对路径写进卡内**），**所有命令的 workdir 都是 `$NF`**，
Python 一律用 `$NF/.venv/Scripts/python.exe`（下称 `python`）。

## When to Use

- 要知道「这本书写到哪了 / 花了多少 / 到审批门没有」
- 要跑或续跑流水线（阶段 1-8）
- 大纲/设定/章节要体检或定向修订
- 完书后要导出 Word、出摘要、写回 Obsidian
- **不适用**：改 `console/` 前端或管线代码 → 用 `novelforge-gui` / `ai-novel-pipeline`

## 入口（三条通道：CLI / HTTP / MCP）

| 情形 | 通道 |
|---|---|
| 跑阶段、审批、体检、精修、看成本、切书档 | **CLI**（`scripts/*.py`），不需要起服务 |
| 要看「正在跑的那个任务」的实时状态 / 流式 token / SSE | **HTTP**（`nf_api`，127.0.0.1:8765） |
| 标准 MCP 客户端（Hermes / Claude Code / Cline） | **MCP**：`scripts/nf_mcp_stdio_bridge.py`（stdio 垫片 → 8766 → 8765；配置段见 `docs/mcp-connect.md` 或 GUI「设置 → 接入其他 Agent」） |

判据：**要「结果」走 CLI，要「正在跑的过程」走 HTTP，客户端常驻接入走 MCP。** 不确定就用 CLI。

**MCP 垫片自启动（2026-10-06）**：垫片启动时若 8765 `/health` 不通会**代拉一次 nf_api**
（连带起 8766 MCP 线程），最多等 12s —— 配置一次即可用。逃生门 `NF_BRIDGE_NO_AUTOSTART=1`
（测试/自检必设，防代拉污染）；项目根解析与 `console/main/project-root.js` 同链
（NF_ROOT → project-root.json → 默认），全废则不代拉。

**给外部 Agent 的启动提示词（kickoff）**：单一事实源 `prompts/agent_kickoff.md`
（GUI「设置 → 接入其他 Agent」可一键复制，自动替换 `{{PROJECT_ROOT}}`）。
用户要开新 Agent 会话时，先让它读这份。

```bash
# 只读入口（零 token、不改任何文件）
python scripts/nfctl.py status          # 全景：书名/阶段/进度/成本/待审批门/产物/素材
python scripts/nfctl.py check           # 环境自检：密钥/配置重复键/素材/端口
python scripts/nfctl.py api /state      # 只读转发 nf_api（省手写 curl）
```

## 自检（开工前必做，失败显式报错）

```bash
cd $NF && python scripts/nfctl.py check
```

四项必须过关，否则**停下报给用户**，不要硬跑：
1. `venv python` 存在（否则用错解释器）
2. `config/system.yaml` 可解析 —— **若有「YAML 重复键」阻塞项，必须报告**（重复键后者静默覆盖前者，曾把 `gates.agent_mode` 配成"看着 true 实际 false"）
3. `API Key 已配置`（Key 在 `$NF/.env`；`_load_env_file` 读**相对路径** → 命令必须在项目根跑，换目录会误报"没配"）
4. `素材非空`（`materials/raw` 或 `original_scraps` 至少一个非空）

## 边界

**允许（只读）**：`nfctl status/check/api <GET>`、`outline_review.py`、`outline_advisor.py`、
`material_review.py`、`polish_review.py`、`cost_report.py`、`estimate_tokens.py`、
`book_summary.py`、`appearances.py`、`snapshot.py --list`、`obsidian_bridge.py scan`。

**需用户明确同意后才做**（改产物，但脚本自带备份）：
跑阶段 `orchestrator.py`、修订 `refine_outline.py` / `refine_chapter.py`、
`setting_refine.py`、`batch_refine.py`、`auto_rewrite.py`、`proofread.py --llm`。

**禁止 Agent 执行**（审批权与最终精修权属于用户）：
`approve.py` / `reject.py` / 切书档 / 改模型 / 改 `gates` / 改提示词。
`gates.agent_mode=true` 时，非 GUI 来源调 `/approve`、`/reject`、
`/project/create|archive|restore|init`、`/config/agent_mode` 直接返回 **403** —— **这是设计，不是故障**。
到了审批门 → 报告「审哪个文件 + 现在什么状态」，等用户自己批。

**永不**：写外部 Obsidian 世界观知识库（本机 vault，路径不入库）；对 `data/`、`history/` 直接 `rm`/`del`/覆盖；
回显 `.env` 里的 Key（只报有/无）。

## 纪律

1. **不静默回退**：`nfctl api` 报"未运行"就是端口没服务，**不是数据为空**。报错比假装没有安全。
2. **退出码优先**：orchestrator 返回 `0` 完成 / `1` 阶段失败 / `2` 预算熔断 / `3` **等审批** / `4` 被中断。
   汇报先给退出码，再给细节。
3. **改配置不擅自动手**：发现 `system.yaml` 重复键、模型不是白名单内 → 报告，不自行改。
4. **两套工作区别混用**：Electron 源码版工作区 = 项目根；安装版 = `%APPDATA%\绒花墨坊\workspace`。
   两边的产物不能互相解释。GUI 启动时若发现 8765 被占会**杀掉占用者**。
5. **提示词与代码分离**：调优改 `prompts/stageN_*.md`，不改脚本。

## 常用处方

**问"写到哪了"** → `nfctl status`；要细节再 `cost_report.py`、`material_review.py`。

**跑书** → `check` 过关 → `estimate_tokens.py` 报价给用户 → `orchestrator.py`（长任务，后台跑）
→ 看退出码；`3` 就报告审批门，**不自己批**。

**"大纲还有问题"** → `outline_review.py`（体检）+ `--trend`（有没有实质进展）
→ 把用户意见写成明确指令 → `refine_outline.py "意见"`（自动备份旧版）→ 再体检，用指标说明"好了没有"。
`--trend` 判 `stalled`/`mixed` 时应换策略，而不是再空跑一轮。

**审批门（阶段2 / 阶段6）** → 阶段2 审 `data/outline/global.md`（先 `outline_review.py`）；
阶段6 审 `data/chapters/refined/`。报告 + 等用户明确同意 → `approve.py --stage N` → `orchestrator.py --from N`。

**整章不要了/重跑某阶段** → `reject.py --stage N "原因"`（先 `--dry-run` 看它清什么）→ `orchestrator.py --from N`。

**看钱** → `cost_report.py`（总览）/ `--by-outline`（大纲逐轮）/ `--by-chapter`；
服务在跑时 `nfctl api /costs/streaming` 看当前 run 实时消耗。

## engine=hermes 实测排雷（2026-10-01，切引擎前必读）

`engine: hermes` = 每次 LLM 调用 → `hermes chat -q` 全工具子会话（吃 Hermes 订阅）。
探针：`HermesClient.run_task` 真跑（写盘/exit code 实证通过），另用 `-v` 日志取证：

1. **模型路由曾是假的（2026-10-01 已修，勿回退）**：`make_client` hermes 分支曾把 `model.*.id`
   透传成 `-m` —— hermes 按其 active provider（小米）解析腾讯云（TokenHub）模型名 →
   **400 Unsupported model → 静默 fallback**（实测落到 LongCat-2.0），未知模型名也不报错（RC=0），
   「配置写 A、实际跑 B」。现按用户定调改为**恒不传 -m**：agent 模式一律用 agent 内部配置的
   模型（流量走 agent 订阅），`model.*` 在 hermes 引擎下完全被忽略。
2. **成本账是虚构的**：子会话 stdout 无 usage → parse_usage 恒 (0,0,0,True) → token 按任务文件
   大小估算，`charge_cost` 再按 RATES 单价记账 → cost_log 金额既非 0 也非真实订阅消耗，
   预算熔断按虚构数触发；`estimate_tokens.py`/GUI 报价同理失真。
3. **停止按钮近乎失效**：stop 只在阶段边界查（orchestrator），stage4 章间不查，
   HermesClient 不接 stop_flag（流式降级为整块回放）→ 长阶段要整个跑完才停；
   单任务另有 900s 硬超时。
4. **TimeoutExpired 绕过 auto_retry**：run_task 超时抛异常 → 穿透 run_stage → run() finally
   收敛为 crashed（不留脏行），但不走「失败→自动重试 2 轮」路径。
5. **审稿门可能静默失守**：chapter_review / proofread --llm / outline_panel 从
   `stdout_tail[-2000:]` 解析 JSON；hermes 的 stdout_tail 是工具流水+ANSI+reasoning 的尾部，
   JSON 窗口被挤占 → 解析失败 → run_review 返回 False → **不报错、直接继续下一阶段**。
   主产物（stage1-6 写文件路径）不受影响（探针实证子会话真写盘）。
6. **无写盘白名单**：direct 引擎产物经 `===FILE===` 协议 + data/** 白名单；hermes 子会话是
   全工具 agent（terminal + 135 工具，实测会用 shell 写文件），约束只靠任务文件提示词。
7. **每任务固定开销 ~30K prompt tokens**（系统提示+工具清单，多数缓存命中）+ 1 次辅助模型
   调用（title_generation，同样先 400 再 fallback）——订阅消耗心里要有数。

### 2026-10-01 二次排雷：上述 2-6 条已全部修复（`tests/unit/test_hermes_client.py` 43 断言锁死）

- **2 / 5（成本虚构 + 审稿门失守）→ `--format stream-json` 一并解决**：收尾 `result` 事件给
  `text`（干净最终消息，无 ANSI/工具流水）作 `stdout_tail`，审稿/校对解析 JSON 不再被挤占；
  `result.tokens` = 真实 usage，`estimated=False`。**成本语义**：`estimate_cost_yuan(provider="hermes")`
  恒返 0 + `estimate_tokens.py` 标 `engine` —— agent 模式走订阅无按量 ¥，**预算熔断对 hermes 不生效是刻意语义**
  （orchestrator 启动时会打印提示），不做「文件大小 × 刊例价」的虚构记账。
- **3 / 4（停止 + 超时）**：`run_task` 改 Popen + `communicate(timeout)` → 超时返回 `exit_code=124`
  结构化失败进 auto_retry/止损链（不再抛 TimeoutExpired 打崩 run）；`run_task_stream` 真流式
  （逐 text 事件回放 + `stop_flag` 杀进程）；stage4 **章间**查 stop、orchestrator **阶段后**查 stop。
  `stopped=True → exit_code=0`（主动停 ≠ 失败，与 direct 同语义）。
- **6（写盘白名单）→ `-t file`**：= patch/read_file/search_files/write_file 四件套，
  terminal/browser/kanban/cron/MCP 全关在门外；`hermes.toolsets` 可覆盖（空串=全量逃生门）。

**三个新坑（本轮实测踩到，勿重犯）**：

1. **`-t` 收 toolset 名，不是工具名**。传 `read_file,write_file,search_files` 得到**空集合** →
   模型照旧吐 `<longcat_tool_call>` 但**永不派发**，`result.text` 是原生 tool_call、**`exit_code=0` 假成功**
   （两次复现）。合法值看 `hermes tools list`：`file` / `web` / `terminal` / `memory` …
2. **Windows 只杀直接子进程必留孤儿**：`hermes` 是 pip 入口 .exe，底下还孵 python；
   孤儿持有 stdout 管道 → `communicate()` 等不到 EOF（实测超时阈值 1s 却 **19.3s** 才返回，
   「停止」后子会话还在跑）。必须 `taskkill /F /T /PID`（已封装 `HermesClient._kill_tree`，
   先 `poll()` 确认存活再杀，防 PID 复用误杀）。git-bash 里 taskkill 用 `-PID -F`（`//PID` 会被 MSYS 吃掉）。
3. **`execute_code` 是持久 kernel**：改完 `scripts/utils/llm_client.py` 后，同一 kernel 里的旧模块
   **不会自动重载** → 拿旧类做验证会得出「代码没生效」的假结论（本轮实测：cmd 还打印旧的 `-t read_file,...`）。
   验证前必须 `del sys.modules['utils.*']` 或新开进程跑（单测/`nfctl test` 都是新进程，不受影响）。

另注：`--run-budget` 实测语义**不是**墙钟秒（budget=10 没切掉 40s 任务），生产杀开关上不猜单位，
已弃用；墙钟上界由 `timeout=900` 承担。`--max-turns` 是 agent 轮次（工具任务需 ≥2，否则 exit 1），默认 60。

## 能力边界（勿夸大）

绒花墨坊**没有 agent 层**：`engine: direct` 是纯 API 直连，无工具调用、无自主多轮、无记忆。
「多轮迭代」全靠外部驱动 —— **Hermes 是脑，它是手**。
它也不生成"N 个可选开工方案"这类创作决策，`outline_advisor.py` 只给候选方向与依据。

端点全清单与请求体形状：`references/api.md`（以 `scripts/nf_api.py` 头部 docstring 为准）。
命令全表与风险标注：`$NF/AGENTS.md`。
