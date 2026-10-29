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

## 细节文档（docs/agents/）—— 按需读取，别当不存在

本手册只留**动手前必须知道的**；细节按主题拆在 `docs/agents/` 下。
**为什么拆**：旧版 76,326 字节，超过 65,536 字节的指令加载预算时系统会**静默截断**，
实测截掉的正是靠后的《能力边界》《硬性约束》—— 手册失效且没有任何报错。
现在主文件 35 KB（原 76 KB），**读全本也很便宜**；下面这些在动到对应模块时读。

| 主题 | 文件 |
|---|---|
| 中文标点归一化（幂等/保守/保护区） | `docs/agents/text-norm.md` |
| 文体（form）与判据适用范围 | `docs/agents/writing-forms.md` |
| 知识库扫描通用化（禁私例当通例） | `docs/agents/kb-scan.md` |
| 长篇规模能力（评审账本/记忆/卷弧/伏笔/残稿） | `docs/agents/long-form.md` |
| 提示词前缀纪律（改 stage*.md 前必读） | `docs/agents/prompt-prefix.md` |
| 关键路径（数据与产物的真源） | `docs/agents/key-paths.md` |
| 运维场景（失败/熔断/打回/升级） | `docs/agents/operations.md` |
| 成书提炼（写完的书 → 正典四表 + 病灶词） | `docs/agents/book-extract.md` |

## 启用流程（用户说"运行 NovelForge"时）

1. 前置检查：
   - `config/project.yaml` 是否已填书名/类型/章节数（书名仍为"示例书名（待填写）"时提醒用户）
   - `materials/raw/` 是否有素材（为空时提醒用户，不要空跑）
2. 启动：`terminal(command="python scripts/orchestrator.py", background=true, notify_on_complete=true, workdir="<repo>")`
   - 必须用项目 .venv 的 python：`<repo>/.venv/Scripts/python.exe`（POSIX 为 `<repo>/.venv/bin/python`）
   - **写成裸 `python` 也不会再炸**：`orchestrator.py` / `nfctl.py` 入口挂了解释器守卫
     `utils/interp_guard.py` —— 当前解释器缺 PyYAML 时 stderr 留痕 `[py-guard]` 后自动切 venv 重跑
     （`NF_PY_GUARD` 防拉锯）；切不动才退 1 并给可行动命令。⚠️ 2026-10-06 试跑连炸的真根因
     就是裸 python（宿主环境缺 yaml），被误诊成「.venv 缺依赖」——venv 里 PyYAML 一直在
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

**单章字数区间（`chapter.target_words`，2026-10-18）**：出厂默认 **[5000, 9000]**
（旧默认 [2000,5500] 让实测 2033 字的章直接过检，用户判定「一章两三千字根本不合格」）。
**总量仍由章数控制**，抬高这里只把单章篇幅拉到合格线。
端点 `GET/POST /config/target_words`（GUI 设置页「预算与止烧 → 单章字数（篇幅口径）」同源），
边界 500~20000 / 500~30000，越界一律 400 并说清区间。
⚠️ **落盘必须是列表 `[low, high]`**：消费方全是 list 语义（`low, high = tw[:2]`、
`is_chapter_complete(path, *tw)`、`tw[0]`），落 dict 会全线炸；`test_target_words.py` 锁死这条。
不设 GUI-only —— 它是**创作决策**不是安全闸门，Agent 可代设但越界拒收。

## 中文标点归一化（2026-10-18，确定性、零 token）

> 详见 **`docs/agents/text-norm.md`**。留在手册里的只有「为什么」：形态错误只能靠确定性替换治理（提示词里早就写着全角标点，产物照旧）；唯一实现 `scripts/utils/text_norm.py`，7 处挂载点 + 幂等/保守/保护区三条护栏。

## 文体（form）—— 决定哪些判据适用（2026-10-20）

> 详见 **`docs/agents/writing-forms.md`**。留在手册里的只有「为什么」：`book.form` 决定哪些判据适用（叙事/散文/书信/单元）；不适应要显式标 N/A，不许给 0 分或满分。书信/单元的写法指导仍未接入（回落叙事并告警）。

## 知识库扫描通用化（2026-10-24，用户点名「不要拿私例当通例」）

> 详见 **`docs/agents/kb-scan.md`**。留在手册里的只有「为什么」：任何「用户的库长什么样」的假设都不许写进代码/提示词/测试夹具；收集走 `vault.rglob("*.md")`，排除项由用户在 GUI 勾（只写 system.local.yaml）。

## 长篇规模能力（2026-10-20，四项一次落地）

> 详见 **`docs/agents/long-form.md`**。留在手册里的只有「为什么」：七维评审账本 / 审美五问 / 三层记忆 / 卷弧滚动 / 残稿采纳 / 伏笔台账 / step 级 checkpoint / 设定库闭环 —— 每项的判据与踩过的坑。

## 发布树卫生：夹具/预设的专名只能取自虚构侧（2026-10-20 立规）

**事故**：我（AI）为写测试夹具，从本机 `Temp/_archive_20261001/_probe_setting.json`
（真实设定集的历史探针快照）里抄了角色名，写进 5 个发布树文件、共 69 处。
`leak_scan` 当时报「零命中」—— 因为它只扫 `git ls-files`（已跟踪文件），
而新文件在提交前全是 **untracked**。`git add` 之后立刻满屏命中。

**两道修正（都已落地）**：

1. `scripts/leak_scan.py` 改为 `git ls-files -c -o --exclude-standard`
   —— 未跟踪但**未忽略**的文件同样会被提交，必须在扫描范围内。
   教训：**"零命中"要先问"扫了几个文件"**（旧 421 / 新 491，差值就是盲区）。
2. **专名来源白名单**（写夹具、写预设、写提示词例子时唯一允许的来源）：
   - `ci/fictional-map.txt` 的**右侧**（虚构侧）；
   - `examples/presets/*`、`examples/sample-book/*`（既有虚构宇宙）；
   - 自己新造的、与任何真实素材无关的名字。
   **禁止**从 `Temp/`、`data/`、`materials/`、`history/`、`deliverables/` 抄任何专名
   —— 那几处就是作者的真实素材。文件名、注释里的例子也算。

审稿口诀：**写夹具时问一句"这个名字我从哪看来的"** —— 答案是"本机某个数据文件"
就停下换名字。

## 命令速查（常用）

| 目的 | 命令（workdir=项目根，用 .venv python） |
|------|------|
| **只读全景 / 自检** | `python scripts/nfctl.py status` · `check` · `doctor` · `test` · **`release-check`（发版前一条命令跑全套）** · `api <GET路径>` |
| 跑流水线 | `python scripts/orchestrator.py`（`--from N` 续跑 · `--stage N` 只跑某阶段 · `--dry-run` 零 token 预演 · `--verbose` 落请求原文） |
| 审批 / 打回 | `python scripts/approve.py --stage N`（`--revoke` 撤销；阶段 1 审批 = 冻结 canon）· `python scripts/reject.py --stage N "原因"` |
| 换书 | `python scripts/switch_book.py --list` / `--archive` / `--restore "书名"`（均需 `--yes`；归档含 `config/` 与 `materials/`） |
| **副产物管理** | `python scripts/artifacts.py`（看清楚）· `--export <id\|all> --to <目录>`（导出）· `--clean <id,…> --yes`（清理，默认 dry-run） |
| **提交前门禁** | `python scripts/quality_gate.py`（未定义名/语法错零容忍）· `python scripts/leak_scan.py --list ci/blacklist.txt`（发布前/CI；**已含未跟踪文件**，提交前跑一次即可，不必先 `git add`） |

> 其余 90+ 条命令（各阶段脚本、体检、精修、沙盒审核、成本、拆书、MCP 自检、各种自检用例…）
> 见 **`docs/commands.md`**。
>
> ⚠️ 为什么搬走（2026-10-03）：那张表 30 KB，把本手册顶到了 **64 KB 的指令加载预算**之上，
> 超限部分会被**静默截断** → 后面的《硬性约束》《能力边界》对自动加载的助手**不可见**，
> 等于手册失效且没有任何报错。现在本文件只留常用入口，完整表在 `docs/commands.md` 里照样可查。

## 成书提炼（把写完的书变成正典，2026-10-27 集成自 SP-1024）

素材卡 → 设定集走阶段 1；**已经写完的一本书 → 正典四表**走成书提炼。
GUI：章节页签「成书提炼」折叠块（预演 → 后台跑 → 合并预览 → 确认）；CLI 同源：
`python scripts/book_extract.py init <书.txt> --name X` → `run X --dry-run`（零 token 预演）→
`run X` → `status X` → `merge X --yes`。产物在 `data/book_extract/X/`（摘要/角色/设定/时间线/
伏笔/冲突/病灶/报告/成本）；**断点续跑**（摘要落盘即算完成，失败章重跑只补它）、
**并库只增不改**（同名角色一个字都不动，写前备份 `data/setting/history/`）、
**零 token 可预演**。需要 `engine: direct`（走 `client._post_chat`），Hermes 子会话下只能预演。
病灶词扫描另有零 token 出口：`python scripts/nfctl.py cliche [--chapter N]`
（`quality_checklist` 末尾也只报告、不阻断验收）。细节 → `docs/agents/book-extract.md`。

## 外部 Agent 接入（Hermes skill + nfctl）

外部 Agent（Hermes / WorkBuddy）接本项目走**三条通道**：

1. **CLI（默认）**：项目脚本，不需要起服务。先跑 `python scripts/nfctl.py status`（全景）
   + `check`（自检），再按上表取具体命令。
2. **HTTP**（`nf_api` on `127.0.0.1:8765`）：只在需要「正在跑的那个任务」的状态、
   流式 token、SSE 时才用。只读转发用 `nfctl.py api <GET路径>`。
3. **MCP**：`nf_mcp.py` 在 **TCP 127.0.0.1:8766** 暴露白名单工具（`tools/list` 返回
   **27 个**：25 个 HTTP loopback + 2 个 LOCAL 直调 —— ⚠️ **数量勿写死**，以
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
`references/api.md`（端点与请求体形状速查 —— 条目随端点增减，勿在此处写死数量）。
与 `ai-novel-pipeline`、`novelforge-gui` 同分类同级；**技能库在 AppData、不进 Git，改动前先留副本**。

**Agent 模式守卫**：`gates.agent_mode = true` 时，非 GUI 来源（GUI 判据 = 双因子：来源头 **加**
主进程签发 token，光带头子不算）调用 `/approve`、`/reject`、`/project/create|archive|restore|init`、
`/config/agent_mode`、**`/config/token_limit`**（止烧阈值：Agent 不得抬高自己的闸门）、
**`/factory/clean`**（出厂样例清理：Agent 可用只读 `/factory/list` 盘点，不得动手搬素材）、
**`/stage/skip`**（跳过阶段 = 标 done+已审批，与审批同族，2026-10-16 补入名单）、
**`/chapters/save`**（手改正文：零 token 直接覆盖三层稿 —— 与「重写（花 token 生成、有备份与
自动恢复）」不是一回事，2026-10-27 补入名单）
返回 **403**。含义是：**外部 Agent 可读、可跑流水线，但不能代替用户审批** —— 这是设计，不是 bug。
另外设置类写入（`/config/agent_mode`、`/config/token_limit`）**无论模式如何都要求 GUI 来源头**；
`/chapters/save` 同档（与 agent_mode 无关也要求双因子 GUI 来源）。
双因子的边界如实声明（2026-10-16 红队 A11）：同机攻击者可自起一份 nf_api 并自铸 `NF_GUI_TOKEN`
整体穿透本名单 —— 但这不比「直接手改 `data/state/*.json`」多出任何能力，同属已声明残余
（防线语义 = 拦住照文档跑命令的 Agent，**不是沙箱**）。

## 提示词前缀纪律（2026-09-23 定，改 `prompts/stage*.md` 前必读）

> 详见 **`docs/agents/prompt-prefix.md`**。留在手册里的只有「为什么」：改 `prompts/stage*.md` 前必读：body 里 `---` 之前必须逐章逐字不变，否则前缀缓存命中率从 77.6% 崩到 1.2%。

## 架构：API 层的域模块拆分（P2）

`scripts/nf_api.py` 原本是 2,872 行的 God Object（91 个端点全挤在
`do_GET`/`do_POST` 两条 elif 链里）。现已拆为 **分发器 + 12 个域模块**，
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
| `setting_state.py` | 设定集冲突/canon/状态回写 | 3 |
| `factory.py` | 出厂清单盘点 / 出厂样例一键归档（0.6.7） | 2 |
| `book_extract.py` | 成书提炼：预演 / 建目录 / 后台跑 / 并库（0.7.13） | 6 |

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
  - `/factory/clean`（出厂样例清理 —— 移动用户素材文件；只读 `/factory/list` 不拦）
  - `/stage/skip`（跳过阶段会把状态标成 done+已审批+skipped —— 与审批同族，2026-10-16 补）
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
- `GET /config/project`、`/config/style_notes`、`/config/agent_mode`、`/config/target_words`
  （单章字数区间：当前值 + 出厂预设 + 合法区间）
- `GET /config/vault_scan`（知识库扫描面：**顶层目录名取自用户库本身** + 各自篇数 +
  是否被排除 + 用户排除项/默认项分列。目录名一律不写进代码 —— 见「知识库扫描通用化」）
- `GET /outline/structure`、`/outline/trend`、`/outline/advise`、`/outline/history`、`/outline/diff`
- `GET /outline/segments`（丙4 分段审阅：分段清单 + 每段正文 + 放行判据回显；**读侧不设闸**）
- `GET /sandbox/queue`、`/sandbox/file`
- `GET /review/report`、`/review/decisions`
- `GET /chapters/quality`、`/chapters/history`
- `GET /costs`、`/costs/summary`、`/costs/streaming`
- `GET /estimate`
- `GET /prompts/list`、`/prompts/get`
- `GET /project/list`、`/project/status`、`/project/archive/tree`、`/project/archive/file`
  （归档**只读**查看：清单 + 单文件预览，越界/超大/二进制有闸）
- `GET /outline/compass`（指南针读取 + 校验结果；没设 → `exists:false`）
- `GET /config/writing_presets`（写作预设清单 + 当前生效值 + 切换历史）
- `GET /factory/presets`（出厂预设清单，只读；铺设走下面的禁用项）

**✅ 可调用**（写入沙盒/草稿类，产物自动进入待审）：
- `POST /outline/save`、`POST /outline/chapters/save`
- `POST /refine/outline`、`POST /refine/chapter`
- `POST /stage/{n}/run`（生成大纲/章节）
- `POST /auto_rewrite/run`
- `POST /sandbox/review`（驳回/退回，但需在 GUI 中确认）
- `POST /export/markdown`、`POST /export/word`（导出成品副本到 output/；只落导出物，不改书稿与进度）
- `POST /config/target_words`（单章字数区间：**创作决策不是安全闸门**，故不设 GUI-only；
  越界值一律 400 拒收并说清合法区间；落盘必须是列表 `[low, high]`）
- `POST /config/vault_skip_dirs`（扫描排除项：**只收目录名**，含 `/` `\` `..` 一律 400；
  只写 `config/system.local.yaml`（私有库目录名不进 Git）；同属创作决策故不设 GUI-only）
- `POST /handoff/export`（导出交接稿到 `data/handoff/第N章后.md`；只落导出物，不改书稿与进度）
- `POST /outline/compass`（指南针：**创作决策**，Agent 可代设；活跃长线 >5 / 终局方向过短
  一律 400 拒收并说清判据，方向变更自动留 history）
- `POST /config/writing_presets/save`、`POST /config/writing_presets/apply`
  （写作预设：**创作决策**，Agent 可代设；未知键一律 400 并列出可选键 ——
  静默忽略等于用户以为存了其实没存）

**🚫 禁止调用**（返回 403）：
- `POST /approve`、`/reject`
- `POST /project/create`、`/project/archive`、`/project/restore`、`/project/init`
- `POST /project/archive/delete`（删归档，默认进回收站；GUI 来源头 + 逐字书名确认双闸）
- `POST /config/agent_mode`（Agent 不得自行切换模式）
- `POST /factory/clean`（出厂样例清理：移动用户工作区素材；只读 `GET /factory/list` 可用）
- `POST /factory/preset/apply`（铺设出厂预设 = 快照 → **归档当前项目** → 铺素材 → 写配置，
  与 `/project/create` 完全同族；列入 `FORBIDDEN_IN_AGENT_MODE` + 端点自身 `_gui_only`
  双因子。只读 `GET /factory/presets` 可用 —— Agent 可盘点，不能动手）
- `POST /outline/segments/set`（大纲分段审批标记 —— 勾一段 = 替用户读一段，归用户在 GUI 分段审阅面板里点；Agent 可读不可勾）
- `POST /stage/skip`（跳过阶段 = 人工拍板放行管线（approved 真写进 progress.json），与审批同族；2026-10-16 补入名单）
- `POST /chapters/save`（手改正文 = 用户本人动作：零 token 直接覆盖 raw/checked/refined，会**静默改写**
  已审阅的稿子且不留模型痕迹；端点另要求双因子 GUI 来源，**与 agent_mode 无关也拦**。
  读侧 `GET /chapters/layers|content` 不拦；整章重写走 `/chapters/rewrite`（Agent 可用，见 MCP `nf_rewrite_chapter`））

CLI 侧同族守卫（2026-10-16 P2，判据同源 `cli_approval_gate`）：`plan_cli.py` 的 select/write/drop/revive、
`switch_book.py` 的 --archive/--restore/--init、`import_docx.py` 的非 --preview 落盘在 agent_mode=true 下
同样拒绝执行；propose、只读命令、--preview、--list 不受限。

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

> 详见 **`docs/agents/key-paths.md`**。留在手册里的只有「为什么」：所有数据/产物路径与它们的真源（设定集、大纲、章节三层、快照、归档、模板、备份……）。

## 运维场景

> 详见 **`docs/agents/operations.md`**。留在手册里的只有「为什么」：阶段失败 / 熔断 / 用户打回 / GUI 排障 / 素材更新 / 跨版本升级的标准处置与退出码。

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