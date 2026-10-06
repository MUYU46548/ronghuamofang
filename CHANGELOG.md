# 更新日志（CHANGELOG）

本项目此前无版本记录（2026-09-19 审查发现「已有 v0.2.0、NSIS 发布等版本行为却无
CHANGELOG」）。本文件从工程审查修复起正式启用。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [v0.6.5] — 2026-10-06 主题颜色亮暗分排 · 解释器守卫（试跑连炸根因）

- **主题颜色亮色/暗色分排**：设置 → 外观与更新，主题色圆圈拆成「亮色主题」（6 个）/「暗色主题」（4 个）两排，各带文字标识 —— 光看颜色圆圈分不清明暗的问题就此解决。分组按 `DARK_THEMES` 动态计算，以后新增主题登记进清单即自动归排。e2e 新增 4 断言（截图 `D11_theme_rows.png`）。
- **解释器守卫 `scripts/utils/interp_guard.py`（试跑连炸五次的真根因）**：裸 `python`（宿主环境，缺 PyYAML）跑 `orchestrator.py` 开跑即 `ModuleNotFoundError`，被误诊成「.venv 缺依赖」—— 实际 venv 里 PyYAML 一直在，**错的从来不是依赖，是解释器**。现 `orchestrator.py` / `nfctl.py` 两入口：缺 PyYAML 时 stderr 留痕 `[py-guard]` 自动切 venv 重跑（`NF_PY_GUARD` 防拉锯）；切不动则 orchestrator 退 1 给可行动命令、`nfctl check` 转阻塞自检项「当前解释器」（`--json` stdout 不受污染）。裸 python 下 `orchestrator --dry-run` 实测跑通、`nfctl check` 实测全绿。
- **验证**：`nfctl test` 81/81（新增 `tests/unit/test_interp_guard.py` 20 断言，含「藏掉 yaml」反证）· `e2e_ux_verify` **121/121** · `vite build` · `quality_gate` BLOCK 0 · `leak_scan` 零命中。

## [v0.6.4] — 2026-10-06 设置页侧栏分类 · Agent 接入三通道闭环 · 固定测试示例工程

- **设置页侧栏分类（7 项）**：原「一条长名单」改为侧边栏页签（引擎与模型 / 预算与止烧 / 通知与退出 / Agent 接入 / 风格与提示词 / 外观与更新 / 诊断与调试），对齐方寸设置页。分类状态存 `localStorage.mofang_settings_tab`，下次进来还停同一页。方寸实测：折叠解决的是「太长」，侧边栏解决的是「找不到」。
- **Agent 接入三通道闭环**：除 MCP 配置段 + kickoff 一键复制外，新增「🤖 让它自装」（对齐方寸 A 路线第三渠道）—— 生成一段贴给目标 Agent（Hermes / Claude Code / Cline）的完整指令（目标配置文件路径 + 按该家格式生成的 MCP 配置段 + 技能卡源 + 自检步骤），由对方现地操作、零写入。指令带完整红线（审批/打回不经过 MCP、禁伪造来源头、禁手写产物冒充执行）。
- **新增「开发态 ≠ 客户端」三条红线**：写入 `prompts/agent_kickoff.md` 与 `ronghuamofang` 技能卡。管线状态文件只有 orchestrator 能写（手写产物 = 试跑不合格）；`{{...}}` 未替换的提示词是模板不是任务（禁止自行填充）；开工先声明工作态（源码态 / 安装版）并全程只用一态。
- **关于页文案清理**：「内部代号 NovelForge」字样移除（仅仓库 README 保留），与 README「对外品牌名：绒花墨坊」口径对齐。
- **固定测试示例工程** `examples/sample-book/`：书名「雾港核心」+ 19 张素材卡 + 预设 `project.yaml`，试跑/重装后复位的已知起点。README 含复位步骤与工作态纪律。
- **e2e 验收**：断言数 78 → 117（新增侧栏 / 分类切换 / 让它自装 / 诊断切分类 / 免责声明可见性等）。

## [v0.6.3] — 2026-10-06 GUI 来源双因子（红队修复）· 版本号不再 vdev · 退出必确认 · 止烧保险开启

- **伪造 `X-Mofang-Source: gui` 可绕过全部 HTTP 写入守卫（红队发现，本次最重）**：
  受保护端点原先**两道闸读同一个可伪造的头** —— `do_POST` 的禁用名单靠它跳过、
  端点内的 `_gui_only` 靠它放行，同源等于一层没有。实测复现：
  `gates.agent_mode=true` 下对 `/project/archive/delete` 无来源头 → 403，
  伪造 gui 头 → **200 且真删成功**（目录进 `_trash`）。影响面覆盖整张禁用名单：
  `/approve`、`/reject`、`/project/create|archive|restore|init`、
  `/project/archive/delete`、`/config/agent_mode`、`/config/token_limit`。
  - **修法 = 双因子**：① 来源头（保留，供审计分类）+ ② **主进程签发的会话 token** ——
    Electron 主进程 `crypto.randomBytes(32)` 生成，`spawn nf_api` 时经 env
    `NF_GUI_TOKEN` 注入，渲染层经 IPC `gui-token:get` 取得后随 `X-Mofang-Token` 头带上。
    token 只存在主进程内存，外部 Agent 走 HTTP 拿不到它，光伪造来源头无用。
    每次启动重新生成（会话级、不落盘）。
  - **判据唯一实现** `agent_guard.is_trusted_gui()`：`do_POST` 与 `_gui_only` 共用同一份，
    否则两边漂移又裂出后门（与拒绝清单同理）。`hmac.compare_digest` 防时序侧信道。
  - **fail-closed**：nf_api 未被注入 token（独立启动 / CLI / 测试）→ 一律不认 GUI 来源；
    渲染层拿不到 token（浏览器预览）→ 受保护端点 403，这是**正确**语义 —— 预览模式
    本就不该能改成本/安全设置。
  - **审计留痕**：自称 gui 但未过双因子的请求记为 `gui(untrusted)` 进
    `agent_audit.jsonl`（对齐该模块「伪造必须显式留痕可追责」口径）。
  - **拒文零通道化**：`_gui_only` 旧文案「缺少 X-Mofang-Source: gui 头」等于教人绕过，
    违反 V5（拒文本身是攻击面），改写为不提任何通道名。
  - CORS `Access-Control-Allow-Headers` 补 `X-Mofang-Token`（漏写即预检失败 →
    前端只看到 `Failed to fetch`，前车之鉴见 2026-09-29 注释）。
- **关于页版本号不再显示 vdev**：打包版 workspace 只播种 `scripts/prompts/templates`，
  `console/package.json` 不在 payload → `/about` 的 version 两个解析路径全落空、
  fallback `"dev"`。现三处（标题徽标 / 运行环境表格 / 版本更新提示 / 底栏）优先显示
  Electron 侧真实版本（主进程 `app:about` IPC 的 `app.getVersion()`，打包版恒可得），
  `/about` 的 version 降为浏览器预览兜底。
- **退出必确认（默认开，设置页可关）**：防误触关窗杀掉 Agent 运行。`quitGuard.shouldGuardClose`
  新增 `confirmOnExit` opt-in 参数（缺省 = 旧行为，旧调用方与既有单测语义不变）；
  设置持久化在 `userData/console-settings.json`（损坏视为默认开）。
- **止烧失控保险开启**：`budget.token_limit.per_request_pause_hermes` 预设
  `false → true`（用户 2026-10-06 定调）。原测试断言理由「一次子会话 1.8 万会撞 8k 误停」
  **已过时** —— 阈值自 2026-10-03 起为 50000，实测 1.8 万远低于它，不会误停。
  预设与出厂配置由用例逐项一致，故 `TOKEN_LIMIT_PRESET` 与 `config/system.yaml` 同步翻转。
- **新增/加固测试**：`test_token_limit_api_http.py` 注入 `NF_GUI_TOKEN` 并加**防伪回归断言**
  （伪造 gui 头无 token → 403、token 不匹配 → 403）；`test_archive_manage.py` 的 `FakeH`
  补双因子头。
- **验证**：`nfctl test` 80/80 · `release-check` 110/110（含打包产物核验 15 文件比对）·
  http 专项 36/36 · quitGuard node 单测 23/23 · `vite build` 通过 ·
  `leak_scan` 零命中 · `quality_gate` BLOCK 0。
- SEED_VERSION 17 → 18（payload `scripts/config` 变更，安装版升级自动刷新工作区）。

---

## [v0.6.2] — 2026-10-06 归档可看可删 · 通知改系统级 · 提示词死字段清理 · 泄露清零

- **归档不再是死胡同**（此前已归档项目每行只有「恢复」，看不了内容也删不掉）：
  - 「查看」= `GET /project/archive/tree|file` 只读清单 + 单文件预览
    （路径越界 / 超 300 KB / 二进制三道闸）；
  - 「删除」= 应用内确认框（勾选护栏 + 逐字输入书名，不用 `window.confirm`），
    默认移入回收站 `data/books/_trash/{书名}__{时间戳}`，勾「彻底删除」才真删；
  - `POST /project/archive/delete` 进 Agent 禁用名单，端点另要求 `X-Mofang-Source: gui`；
  - `list_books()` 跳过 `_trash`（否则回收站会以「一本叫 _trash 的书」出现在归档列表）。
- **审批门通知改走主进程系统通知**：修收件箱「🔔 通知权限」死按钮 —— 旧实现
  `Notification.requestPermission()` 只在 permission 为 `default` 时才动作，打包态通常已是
  granted/denied → 点了毫无反应、且授权结果从不回显。现走 IPC `notify:gate` → 主进程
  `new Notification().show()`（OS 级、无需授权，方寸同款方案），浏览器预览模式回退旧实现；
  按钮本身变成开关，旁边显示当前通道（「系统级通知，无需授权」）。
- **提示词死字段清理**：15 个模板的 `model: hy3` 从**三份副本**（源码树 / 桌面工作区 /
  安装包 payload）删除 —— 全项目无一处代码读它，真源是 `config/system.yaml` 的 `model.*`；
  「复制提示词」按钮剥掉 frontmatter 只给任务正文；新增
  `tests/unit/test_prompt_frontmatter.py` 防回潮。
- **修 `--root` 下两个数据源**：`_set_root()` 现同步刷新 `switch_book` 的
  `PROJECT_ROOT/DATA_DIR/BOOKS_DIR` —— 否则 `/project/list` 列的是**脚本所在项目**的归档、
  归档查看/删除按 `ROOT` 读，同一个项目页两边会静默不一致。
- **新增测试**：`tests/unit/test_archive_manage.py`（43 断言：路径穿越、删除四道闸、根唯一性）、
  `tests/unit/test_prompt_frontmatter.py`、`tests/e2e/e2e_new_features.py`
  （25 断言：自建假归档夹具 + 动态空闲端口 + 跑完自清，不碰用户在用的 8765）。
- **验证**：`nfctl test` 80/80 · `e2e_ux_verify` 110/110 · 新增 e2e 25/25 ·
  真 Electron（CDP 附着）通知 IPC 8/8 · `node --check` main+preload 通过。
- **泄露门禁清零（leak_scan 20 → 0）**：清掉散在免责声明文案 / 文档 / 接线卡里的
  **身份词与本机绝对路径**（`E:/…` 一类）—— 面向用户的产品不写死私人路径；
  接线卡改由克隆位置解析 `$NF`，词表里的路径条目保留作后续防回潮。
- SEED_VERSION 16 → 17（payload scripts/prompts 变更，安装版升级自动刷新工作区）。

## [Unreleased] — 2026-10-06 一键复制阶段提示词给 Agent

- **流水线页签每阶段「复制提示词」按钮（仅 Agent 模式可见）**：点一下就把该阶段主提示词全文复制到剪贴板，Agent 拿到就知道接下来干什么，不用每次用自然语言重新描述。按钮在「流式运行」之后、「确认」之前，仅 `agentMode` 开启时显示。
- **disclaimer.js v1.1**：作者措辞微调（去词间空格、「」→""、③标题改「绒花墨坊作者不占有您的作品」等），ack 键升版，全员重新强制确认。
- **e2e 断言适配**：ack 键按前缀匹配（兼容 v1 → v1.1 升级）。

## [v0.6.1] — 2026-10-06 对齐方寸：接入页 · 诊断调试 · MCP 自启动 · kickoff

- **GUI「设置 → 接入其他 Agent」**：按运行态（安装版/源码版）生成 MCP 配置段
  （Hermes YAML / Claude·Cline JSON，路径真实可复制）+ 一键复制 + 打开目标配置文件
  （主进程固定三目标，不收渲染层任意路径）；MCP 8766 状态实时探针。
- **kickoff 启动提示词**：单一事实源 `prompts/agent_kickoff.md`（防外部 AI 从头重建 /
  绕守卫的第一道闸），同页一键复制并自动替换 `{{PROJECT_ROOT}}`。
- **MCP stdio 垫片自启动**：8765 `/health` 不通时代拉一次 nf_api（连带 8766 MCP 线程），
  最多等 12s；项目根解析与 `project-root.js` 同链（NF_ROOT → project-root.json → 默认），
  全废不代拉；逃生门 `NF_BRIDGE_NO_AUTOSTART=1` / `--no-autostart`（测试/握手自检必设）。
  新增 `tests/unit/test_bridge_autostart.py`（18 断言）。
- **调试入口（对齐方寸「诊断日志 / 🩺 诊断」）**：顶栏「📋 日志」常驻按钮 +
  设置页「诊断与调试」块（运行形态/健康态/代码根/数据根/日志路径 + 查看运行日志 /
  打开日志文件 / 复制诊断信息 / DevTools 开关 / 重启后端 API——运行中禁用）+
  快捷键 Ctrl+Shift+I / F12（打包态也能开 DevTools）。
- **接线卡仓库真源**：`skills/worldbuilding/ronghuamofang/` 入仓（此前只有 AppData
  副本，不符接线卡纪律）；方寸 `fangcun-bridge` 卡「绒花墨坊无 MCP 层」过时表述已在
  方寸仓修正（该仓在施工，改动留工作树待其合并）。
- **验证**：`nfctl test` 78/78 · `e2e_ux_verify` 107/107（含新块 DOM + 几何断言）·
  quality_gate 通过 · 握手自检 9/9。
- SEED_VERSION 15 → 16（payload scripts/prompts 变更，安装版升级自动刷新工作区）。

## [Unreleased] — 2026-10-05 免责声明首启强制确认（用户 00:34 需求）

- **新增 `DisclaimerDialog.vue` + `disclaimer.js`（A/B 双版文案单一事实源）**：
  A 版首启强制（滚动读完+勾选「我已阅读并同意」方可进入主界面；「不同意并退出」=关软件；
  tab 焦点困于弹窗内=真锁模态；ESC 无旁路；弹窗内可切完整版全文），
  B 版完整八节常驻查看（六厂商对照表含表内）。
- **三个随时查看入口**：关于弹窗「免责声明」按钮 / 命令面板（Ctrl+K 搜「免责」）/
  设置页 · 服务商与 API Key 区「查看完整声明」。查看模式显示既往确认时间。
- **文案三源合流（终稿 v2 收口）**：防封号段=作者 10-04 11:32 定稿原文（OpenClaw/OpenCode，原样照录）；
  其余=哨兵法务草案（不主张任何权利/标识办法第十条/厂商对照表）；
  梅林规格六件与验收断言 5 条全对照落地（联网口径按 updater autoDownload=true +
  autoInstallOnAppQuit=true 实测写准「自动检查并下载软件更新，退出时自动安装；
  无遥测」，哨兵 00:47 复核闭环）。
- **版本化重确认**：`mofang_disclaimer_ack_v1` 键随 `DISCLAIMER_VERSION` 变化——
  条款实质变更时升版 → 全员重新强制确认。模板与版本记录见 `docs/disclaimer-template.md`
  （同理念产品如明鉴可直接复用，键前缀独立）。
- 渲染层零依赖新增；主进程/orchestrator 零改动。vite build 通过（412→416 kB），
  模块冒烟 14/14（A/B 结构/定稿原文一致性/两决策在案/表格/生命周期/容错）。
- **终稿 v2 裁决落地（作者 10:45）**：删 B§5「撞稿风险」条（余四条重排 1-4）+ 删 B§8
  未成年人段；A 版七段零改动；`DISCLAIMER_VERSION` 保持 v1（首上线无存量 ack）。
  `disclaimer.js` ↔ 终稿 md 程序化逐段比对 73/73 通过。原划线三件收口，仅余措辞微调
  （改字入口仍只 `disclaimer.js` 一个文件）。
- **真机验收（e2e_ux_verify 升级）**：全新 profile 下首启强制门会拦死后续所有点击
  （实测卡在「跳过确认框」，`disclaimer-mask intercepts pointer events`）→ 新增
  `settle_disclaimer()`：**先跑门的行为断言（role=dialog / 未滚到底禁用 / ESC 无旁路 /
  滚到底→勾选→同意→ack 落盘），再走真实勾选流放行**——不是绕过，是把这道闸纳入验收；
  另加命令面板「查看免责声明」与设置页入口两条断言。全量 91/0 通过，
  几何验收 17/17（弹窗居中/无溢出/按钮不重叠/B 版八节+六厂商表+两刀已删）。

## [Unreleased] — 2026-10-03 止烧 + 静默失败收口（P0/P1/P2 + 前置：控制台安全网）

### 🔴 P0 止烧

**engine: hermes 下整条流水线原本没有任何止烧闸门。** `estimate_cost_yuan` 对
`provider="hermes"` **恒返 0**（订阅流量，刻意语义）→ `cost_log.cost_yuan` 全是 0 →
`budget.limit_yuan` 永不命中。一次跑几十章可以一路烧到没边。

- **token 级熔断**（`config/system.yaml` → `budget.token_limit`，显眼处）：
  `max_total_tokens`（单轮累计输入+输出，默认 **1000 万**）与 `per_request_max_tokens`
  （单请求输出上限，默认 **50000**）。判据只有一份 `CostTracker.status_detail()`
  （金额 / token 累计 / token 单次），`status()` 退化为它的二元组包装 →
  老调用点零改动。触发后走**现有** budget/pause（`progress.budget.paused` + 退出码 2），
  **没有新状态机**；阶段内（stage4 逐章 / stage5 分批 / stage6 分卷）也查。
  熔断日志点明**是哪条判据命中 + 该调哪个键**（旧日志在 hermes 下只会打「已用 0.00 元」）。
  （阈值初值 300 万/8000，同日按用户口径调高并做成可配 —— 见下方「阈值调高」。）
- **单请求上限落到调用点**：direct 引擎真把 `max_tokens` 设进 payload（上限优先，
  不被调用点参数顶掉）；hermes **没有这个旋钮** → 只比对 + 大声告警，
  要按次熔断就开 `per_request_pause_hermes`（默认关：实测 hermes 一次 stage1 子会话
  输出就有 1.8 万 token，拿 8k 当判据会**第一轮就误停**）。
- 启动横幅打印生效的 token 闸门与 `gates`（`auto_rewrite` 误开 = 双倍烧，摆到台面上）；
  `gates.auto_rewrite` 本机确认 **false**。

### 🔴 P0 审稿 JSON 断裂（一断报废整轮 → 兜底/重试/明确停）

- `chapter_review` 原先自带**第二套**手写 JSON 解析，**没有断裂兜底** → 被 max_tokens
  截断（只差收尾括号）的报告一律解析失败，且**一次就 return False**：整轮审稿
  （输入动辄几十万 token）白烧，返回的还是半截原文。
- 现统一走 `utils.validator.parse_llm_json`（括号配对补全 + 剥 `<think>`/去围栏）；
  仍失败 → `gates.review_retries`（默认 1）重试 → 仍败则**原文隔离**
  `data/state/review_raw/` + 可行动报错 + 审稿门 fail-closed（不放行 stage5/6）。
  调用本身失败（退出码非零）**不重试**（同一个上限只会再截断一次，纯白烧）。
- 新增契约判据：报告必须是带 `chapters` 数组的对象 —— 只判「能解析成 JSON」的话，
  模型回一句 `{"error": …}` 会被当报告收下，渲染出一份假的「零问题」报告（比报错更坏）。

### 🟠 P1 缓存与输入构造

- **多文件任务统一请求头**：`segment_requests` 把**变量放到最末**
  （`第 i/N 个 + 路径`），常量说明整段落进公共前缀 —— 旧写法指令自身第 3 个字符就分叉，
  那句「严禁输出其他文件的内容块」每个子请求各成一份独立前缀。新增
  `common_prefix_len()` 作为唯一判据，导出前缀占比 99.30%（反证：变量前置 0.04%）。
- **输入段格式不符 → 明确报错**：`extract_input_paths` 新增诊断出口（段起始行号 +
  逐条**绝对行号/原文/原因**），`inline_inputs` 在诊断到「有路径但解析器不认」时抛
  `InputSectionFormatError` 并打印正确写法 —— 不再静默返空。这条是 stage5 空稿事故的
  同类根因（模板漏写 `- ` 前缀 →「该行从未被内联」而照打「检查完成」）；
  连带的第二个静默口子「目录引用指向不存在的目录」也一并报。

### 🟡 P2 守卫与配置完整性

- **Agent 模式 CLI 后门堵上**：HTTP 层对 `/approve` 早有 403 守卫，但
  `python scripts/approve.py --stage 2` 原先**没有任何检查** —— 外部 Agent 照文档跑一条
  命令就能替用户拍板（`reject.py` 同）。现两侧共用 `utils/agent_guard.py`
  （禁止清单 / 模式判定 / 审计落盘各一份）；CLI 侧「人工来源」取证 =
  交互式 TTY（stdin+stdout 都要）或 `MOFANG_SOURCE=gui` 或显式 `--human`，
  守卫**在任何写操作之前**（progress.json 不被改）。⚠️ 不是沙箱，别夸大。
- **project.yaml 重复键必须报错**：`switch_book.current_book_name` 原先裸 `safe_load`
  （PyYAML 对重复键**静默取后值**）→ 换书可能把内容归档到**错误书名**下。
  现走带重复键检测的 loader、**只要文件存在就校验**、解析失败抛 ValueError
  （CLI 收敛成一行话，不产生任何归档）。顺带挖出两个静默损坏：
  ① 该函数读的是**旧布局** `data/progress.json`（现网在 `data/state/`）→「progress 优先」
  从未生效；② `set_book_fields` 插入新键时把缩进**写死 4 空格**（本项目 book 用 2 空格）
  → 写出的骨架 project.yaml **是非法 YAML**，而读取侧的 `except: return ""` 把它吞了。

### 🔴 系统性问题：管道 stdout 下的非 GBK 字符会打死流水线

stdout 被捕获（GUI/后台任务/测试运行器）时 Python 用 cp936，而 `¥`/`⚠️`/`💰`/`✅`
**不在 GBK 里** → `UnicodeEncodeError`。实测后果都不是"日志难看"而是行为错：

- `orchestrator.py --dry-run` 在 `engine: hermes` 下**直接退 1**（第一行横幅含 `¥`）；
- `quality_gate.py` 的**通过行**含 ✅ → 门禁明明通过却退 1（**假红**），
  而 release-check/CI 靠退出码判定；`stage4` 的 KB 注入失败分支在 `except` 里打印 ⚠
  → **异常处理自己变成新的异常源**；
- `leak_scan.py`（发布前/CI 泄露门禁）同理：本仓词表 62 条、扫 289 个文件、**命中 0**，
  退出码却是 1 —— 一个"永远红"的门禁等于没有门禁；`quality_checklist.py`
  （orchestrator 收尾闸门）同样假红。

修法分两层，判据仍只有一份（`utils/console.ensure_utf8_stdout()`）：
① **包级挂钩** —— `utils/__init__.py` import 时调一次（项目里每个脚本都会
`from utils…`，覆盖面最广且只需维护一处）；② 不 import utils 的四个纯 stdlib 脚本
（leak_scan / quality_checklist / nf_mcp_handshake_check / nf_api_selftest）在 `main()` 显式调。
刻意**不改 errors**：UTF-8 能编码任何正常字符，改成 `replace` 反而会在 MCP stdio
这类协议流上静默毁数据。
连带修 `nfctl.py test` 的**结果不可信**（子进程输出按 cp936 解 UTF-8 → 解码异常让
`proc.stdout` 变 None → 被记成「错误: 'NoneType' …」，实测 7 个用例的结果根本没读到；
现按项目惯例给子进程 `PYTHONIOENCODING=utf-8`）。

**回归**：`tests/unit/test_failure_paths.py` 59 PASS/3 FAIL → **69 PASS / 0 FAIL**；
之前"挂"的 7 个用例全部 rc=0；`quality_gate` / `leak_scan` / `quality_checklist`
（管道）退 1 → **0**；`orchestrator --dry-run`（管道）退 1 → **0**；
`nfctl test` **68/68 全通过**。

### 🧪 新增自检（207 断言，全部零 LLM/零网络）

`test_token_circuit.py`（54）· `test_review_json_repair.py`（33）·
`test_segment_cache_prefix.py`（21）· `test_input_section_format.py`（32）·
`test_agent_mode_cli_guard.py`（49）· `test_project_yaml_dup_keys.py`（18）。

⚠️ 事故与教训（写进用例护栏）：新用例一度用「chdir 到临时目录 + 跑真实仓库的
`switch_book.py`」的方式，而该脚本的 `PROJECT_ROOT` 取自 `__file__`（不是 CWD）→
**把真实工作区归档了**。已当场恢复并逐文件核对（0 文件丢失 / 0 内容差异 / 0 文件被重写），
新用例改为「复制脚本进临时根」并加断言护栏。

---

## [Unreleased] — 2026-10-03（第二轮）桌面端退出 · 配置严格化 · 阈值可配 · 闸门收口

### 🔴 桌面端「关不掉」（用户复发报告）

现象：应用无法退出，只能强杀。根因不止一处，且都是**静默**的：

- 渲染进程 `beforeunload` + `returnValue` 会在 Electron 里唤起**原生**确认对话框 ——
  而本项目的 GUI 纪律明令不用原生对话框（不可靠），且该守卫的触发条件是
  「本轮完成 && 未审批」，一个**长期存在**的状态 → 每次关窗都被它拦。
- 主进程**没有** `close` 处理器：窗口关了，子进程未必跟着走。
- `stopApi()` 只杀直接子进程，而 `nf_api` 会再 spawn 孙进程（stage 子会话）→
  8765 端口被孙进程继续占着。
- 端口探测是**无上限递归**：一次失败就再递归一次，永远退不出去。

修法：`console/main/quitGuard.js`（纯函数，可单测）+ 主进程状态机
（`rendererBusy/isQuitting/lastCloseAskAt/closeAskTimer`）、`killTree()` 走
`taskkill /F /T`（Windows 上杀整棵树）、`askRendererToClose()` 带 5 秒看门狗、
`forceQuit()` 兜底；渲染侧改为**应用内**对话框（`app:quit-confirmed/canceled` IPC）。
自检 `tests/unit/test_desktop_quit_guard.py`（31 断言）；另 `console/tests/quitGuard.test.js` 17 条。

### 🔧 配置统一严格 loader（A1 + A2）

`config/*.yaml` 原先散落 ~40 处裸 `yaml.safe_load` —— PyYAML 对重复键**静默取后值**。
历史事故（`project.yaml` 的 `user_outline`、`system.yaml` 的 `gates.agent_mode` 双写）
只做到了「`nfctl check` 能查出来」，**loader 并不拒绝**；而本轮止烧阈值恰恰住在这份
文件里 → 配置被静默覆盖 = 熔断闸门看着设好了、实际没生效（与「¥ 记账恒 0」同一类失败）。

- 新增 **`utils/config_io.py`**：配置类 YAML 的**唯一读写入口**。
  `load_config_yaml`（重复键/语法错 → `ConfigError`，带**行号 + 危害说明 + 体检入口**；
  文件不存在 → 返回 default）、`deep_merge` / `load_pipeline_config`
  （system ← local 深合并 + project，三份全严格）、
  `set_section_scalar(s)`（**定向改写**：嵌套段路径、缺段补建、写前备份、
  写后**按值**回读校验；批量版一次备份一次落盘，避免「改了一半」）。
- 21 个文件 / ~40 处调用点全部换掉（含 `nf_api_domains/models.py` 的
  `api.yaml.safe_load(...)` 与 13 个文件里随之失效的 `import yaml`，含函数内缩进写法）。
  `utils/project_config` 删掉**自己那份** `_UniqueKeyLoader`，改为委托
  （同一判据写两遍 = 迟早只改一处 —— 本轮的坑正是「当年只修了 project.yaml」）。
- **结构性护栏**（`tests/unit/test_config_io.py` 第 5 节）：`scripts/**` 出现
  `yaml.safe_load(` 即报；豁免名单**每条必须写理由**并会被打印
  （模板 frontmatter / nfctl 只读诊断 / 设定集数据文件），且带反证与「名单不许变成空话」断言。
- `quality_gate.py`：**语法错从 WARN 提为 BLOCK**。理由是本轮亲历 ——
  改 orchestrator 时留下一个 IndentationError，门禁照样显示「BLOCK 0 + 通过」，
  而全量测试集体 ImportError、真因被埋。此改动随后**立刻抓到**一次真错误
  （机械替换把 import 插进了多行 import 语句内部；另一次抓到 `nfctl.py` 的未定义名 `cfg`）。

### 🎚 止烧阈值调高 + 设置页可配（出厂预设 + 边界校验）

用户口径：「单章 5000~15000 字，常态 8000 字左右」「阈值调高：1000 万/轮、50000」
「参数可由用户在设置中手动配置，并保留一套默认预设，防止用户乱改改坏」。

- `config/system.yaml`：`per_request_max_tokens: 8000 → 50000`、
  `max_total_tokens: 3000000 → 10000000`（把「为什么是这两个数」写进注释：
  50000 拦的是**失控生成**，不是正常写长章）。
- `cost_tracker`：`TOKEN_LIMIT_PRESET`（=「恢复默认」唯一取值来源）、
  `TOKEN_LIMIT_BOUNDS`（累计 10 万~2 亿 / 单请求 1000~50 万 / warn 0.1~1）、
  `validate_token_limit()`（写入口校验，报错说清哪一项与区间）。
- **闸门不许静默消失**：`normalize_token_limit()` 读到越界值 → 回落预设并打印 WARN；
  「enabled 但两项都是 0（= 没有闸门）」也回落累计上限。设成 0 在今天语义里是
  「不限制」—— 与本轮修的「¥ 记账恒 0」是同一类坑。
- API：`GET/POST /config/token_limit`（写入口只认 GUI 来源并列入
  `agent_guard.FORBIDDEN_IN_AGENT_MODE` —— 外部 Agent 若能抬高自己的闸门，止烧就是摆设）；
  `handle_config_agent_mode_set` 原先自带的第二份正则写入也收敛到 `config_io`。
- GUI：设置页「**预算与止烧**」面板（四个数值 + 区间提示 + 保存 / 恢复默认预设 + 明确回显）。

### 👁 token 闸门可见性（hermes 下不显示 = 让用户盲调阈值）

- `nfctl status`：新增 `token 闸门: 已用 N / 上限 M（x%）（本 run #id）· 单请求上限 K`；
  未启用时**明说**「未启用（hermes 下金额阈值无效 → 本轮没有止烧闸门）」。
- `GET /state` → `budget.tokens_used / tokens_run_id / token_limit / token_pct`。
- `estimate_tokens.py`：结果新增 `token_limit` 对照（已用 + 本轮估算 = 预计，超限 `exceeds`），
  文本模式打印同一行；撞闸门时给「届时熔断暂停，可调大上限或缩小范围」。
- GUI 流水线条新增「token 闸门」一项（数字真来自 `/state`）。

### 🚦 质量验收闸门：产物缺失 = 失败（用户拍板）

> 章节文件不存在，如果是跑了但没落盘，当然不是预期的结果。这绝对有问题。

`quality_checklist.py` 旧实现对缺失章节只打 `[SKIP] 文件不存在` 就 `continue`，
`all_pass` 保持 True → 末尾照打「**全部章节通过验收 ✓**」并退 0 —— 而它正是
orchestrator 的收尾闸门（`gates.quality_gate`）。现：缺失 → FAIL + 两个候选路径 +
含义说明；`--allow-missing` 为**显式**逃生门且摘要必须写「未经验收」（跳过 ≠ 通过）；
`book.chapters = 0` 这类**空集合也直接报错**（vacuous truth 是门禁最阴的假绿）。

### 🗂 副产物管理台（看清 / 导出 / 清理）

用户第 4 条：「非报告类副产物找个专门的地方丢，用户自己决定复用、导出或清理。」

流水线会在十来处目录铺东西，但性质完全不同，此前**没有任何统一入口**（要么翻文档目录表，
要么根本不知道存在）。新增 `scripts/artifacts.py`：一屏列出路径 / 文件数 / 体积 / 最老最新 /
**安全等级** / 是什么 / 怎么复用，并提供导出与清理。三档安全等级：

- `safe`（截断稿、失败审稿原文、`--verbose` 的 LLM 原文、任务文件、拆书切分）→ 随时可清；
- `backup`（config / prompts / 大纲 / 章节 / 设定集的历史版本）→ 按天或按量收口，也能整体导出；
- **`keep`（快照 `history/`、归档书 `data/books/`、报告、待审沙盒、账本 `logs/runs.db`）
  → 一律拒绝清理**，并把"该用哪个工具"指出来（快照→`snapshot.py --restore`、
  归档书→`switch_book.py --restore`、沙盒→审核流程）。

`--export <id,…|all> --to <目录>`（默认打包 zip，**不动源文件**）、
`--clean <id,…> [--older-than 天] [--keep-last N] --yes`（**默认 dry-run**，
与 `snapshot.py --restore` 同惯例）。自检 `tests/unit/test_artifacts.py`（**31 断言**，
临时根上跑：名录唯一/相对/不逃逸 · keep 拒绝且文件仍在 · dry-run 不删 ·
`--yes` 真删且 `--keep-last` 生效 · zip 内容正确且源文件未变 · 未知 id 退非零并列出可用项）。

### 🧪 本轮新增自检（本批 6 个用例 / 194 断言，全部零 LLM）`test_config_io.py`（38）· `test_quality_gate_syntax.py`（10）·
`test_token_limit_config.py`（50）· `test_desktop_quit_guard.py`（31）·
`test_quality_checklist_gate.py`（20）· `test_artifacts.py`（31）；
HTTP：`test_token_limit_api_http.py`（33）。
另有两个「让假绿无处藏」的护栏：`test_runner_hygiene.py`（8）与
`test_style_v2.py`（由纯 print 改造为 19 断言）。

**e2e 真机视觉验收 76 → 78 通过 / 0 失败**，并修掉它两个会改**用户真实数据**的隐患：

- ⚠️ **事故（已还原）**：真后端段落原先假定工作区是「1-4 已完成」并写死
  「下一步 = 运行阶段 5」；本轮工作区停在**审批门**，于是那次 `Space`（= 执行下一步）
  把用户的 `stage1.approved` 真按成了 true。已按原始 JSON 形状手工还原
  （删掉 `approved` 键，不是写 false）并核对 `nfctl status` 回到「待人工确认: 阶段 1」。
  现改为**从 `/state` 推导**期望值，且下一步是审批门时**整段跳过 Space**（附 SKIP 原因）。
- About 断言硬编码**某台机器的绝对路径**（换机器假红，且把私人路径写进仓库源码）
  → 改为从 `ROOT` 推导。

**全量回归**：`nfctl test` **73/73 全通过**；`quality_gate` BLOCK 0 / WARN 39（未增）；
`leak_scan` 0 命中；`prompts/` 全程未动。

### 🔒 截断类失败不再自动重试（第二处「白烧」）

`finish_reason == "length"` 时我们隔离残缺稿，但留下的只有**给人看**的 txt ——
机器无从判断"这次失败是截断类"，于是 `auto_retry`（默认开、默认 2 轮）照样重试：
**同一个 `max_tokens` 上限再跑一次只会再截断一次**，白烧一倍输入（几万~几十万 token）。
现：截断时同时写机器可读标记 `data/state/truncated/last.json`（判据 = `utils/truncation`，
写 `mark()` / 读 `after(阶段开始时刻)`，**按时间戳比较**而不是"文件存在" ——
标记是累积的，只看存在会把之后每次失败都误判成截断）。orchestrator 的重试判据抽成纯函数
`should_auto_retry()`：截断一票否决（开关开着也不重试），拒绝理由给出根因 + 为什么 + 该调哪个键。
自检 `test_truncation_no_retry.py`（28 断言，含反证与「真实工作区的标记未被碰到」）。

### 🔒 MCP 暴露面护栏（外部 Agent 够不着禁止端点）

`tests/unit/test_mcp_approval_surface.py`（9 断言，零网络）：25 个工具**没有任何一个**
指向 `agent_guard.FORBIDDEN_IN_AGENT_MODE` 的端点（含 `/config/token_limit`）·
名字里不许出现禁止语义（防改名绕过）· `nf_mcp` 不另立禁止清单（单一事实来源在 `agent_guard`）·
每个 HTTP 工具的端点在 `nf_api` 分发链里真实存在（防死工具）· 数量不写死。

### 📚 AGENTS.md 撞到 64 KB 指令加载预算（静默截断 = 手册半失效）

`AGENTS.md` 涨到 **66508 字节**，超过工作区指令预算 65536 —— 超限部分被**静默截断**，
即《硬性约束》《能力边界》对自动加载本手册的助手**不可见**，且没有任何报错
（本项目最忌讳的"静默失效"，这次发生在自己的手册上）。罪魁是《命令速查》那张 30 KB 的表。
现整表迁到 **`docs/commands.md`**，`AGENTS.md` 只留常用六条 + 指针 →
**66508 → 38123 字节**，预算之内。

### 🔧 switch_book：说清「将操作哪个根」+ 修 CP936 管道下打印即崩

`PROJECT_ROOT` 取自 `__file__`（不是 CWD），运行时**完全看不出来** —— 本轮就因此把真实工作区
归档过一次（已逐文件核对恢复）。现在任何写操作之前先打印将操作的根，CWD 不同则明确提示；
`--list` 不啰嗦。顺带修掉一个真 bug：新加的 `⚠` 在 cp936 管道下把命令打死
（本脚本只在函数内延迟 import utils → 包级安全网尚未生效）→ `main()` 显式调一次 + 文案改 GBK 安全字符。
`test_switch_book_scope.py` +5 断言（26 全过）。

**最终回归**：`nfctl test` **77/77 全过** · e2e 真机视觉验收 **78/78** ·
`quality_gate` BLOCK 0 / WARN 39（未增）· `leak_scan` 0 命中（扫 302 个文件）。


---

## [0.4.2] — 2026-10-03 协议两个高危雷 · 素材库状态机 · 进料节流 · 三薄工具

### 🔴 两个高危**静默失败**（协议层，会污染产物）

**① 结束标记不匹配 → 整块被丢 → 兜底把带标记的原文写进章节**
`parse_ops` 要求块后有 `===END===`，而 `prompts/stage5_check.md` 教模型写的是
`===END FILE===` → 解析器不认 → 每批的块**全部被丢弃** → `_apply_ops` 的兜底把
**整段模型输出**（含 `===FILE:` 标记行）写进章节文件。实证：`data/chapters/raw/01.md`、
`02.md` 的首行就是那条标记行。
- 修：`END_RE` 兼容带 tag 的写法；找不到 END **不再丢块**（改用「下一个块之前」收边 +
  告警）；提示词统一为 `===END===`；读取侧再加 `strip_protocol_markers()` 兜一道
  （stage7 合并正文、stage4 上一章衔接都会剥）。

**② 阶段 5 空转，却报「逻辑检查完成」**
雷 ① 的直接后果：stage5 每批块被丢弃 → 走「复制 raw → checked」兜底 → 然后**照打**
「逻辑检查完成，报告: data/outline/check_report.md」——**而该报告从未落盘**，
`checked/03.md` 与 `raw/03.md` 逐字节相同。
- 修：收尾自检，把「兜底复制过哪几章」「报告是否落盘」变成**显式警告**，同时写进
  返回消息与 `progress.warning`（旧实现只打一行普通日志，下一行照打「阶段 5 完成」）。

### 素材库状态机（**确定性、零 token**，不给 LLM 自报置信度的机会）
- **冲突分级**：同名同字段、两边非空且字面不同 → **报人**（目标个位数）；
  子串包含 / 英文名·别名 / 「待填充」→ 自动归并。
- **墓碑**：被否决的条目下一轮**不得复活**，归并提示词真的携带；损坏文件**不静默**。
- **canon 快照**：`approve.py --stage 1`（设定集定稿）自动冻结；stage2/3 只读快照 →
  改素材不再悄悄扰动已定稿的大纲。`--revoke` 撤掉。
- **多回合继承**：上一轮待裁决分歧（带双方出处）进本轮提示词，并标注「继承、不得当成已定」；
  分歧数回升会报警。
- **进料节流（B1）**：stage1 多回合迭代时只把**未定稿**素材交给模型（adopted 条目原文
  不再进 prompt，改由 canon 快照承担）。⚠️ 节流的闸门在 `llm_client.inline_inputs`
  对**目录**的 glob 全量内联，不在提示词模板 —— 「不引用目录」是生效的必要条件。
- **canon 自动重冻（B4）**：stage1 收尾时若 canon 已存在，按条目级 diff 重冻并打印
  **变更字段**。首轮**不自动造** canon（定稿必须走审批）。

### 外部 Agent 三薄工具（MCP 25 个工具）
`GET /setting/conflicts`（素材冲突清单，零 token）· `GET /setting/canon[?full=1]`
（canon 元信息，默认不含全文）· `POST /setting/status`（人工拍板：`reject` 必填原因且
**先写墓碑**，不依赖条目存在；人工结果带 `status_source=human`，**压过**后续机器判定）。
对应 MCP 工具 `nf_get_setting_conflicts` / `nf_get_canon` / `nf_set_material_status`。

### 进料与写作的节流/续接开关（**默认不改行为**）
- `gates.material_autonomy`：本机默认 `false` = **陪跑**（stage1 归并后停在审批门等拍板）；
  `true` = 自主。**键缺失 = 保持改动前行为**。
- `hermes.session_continuation`：默认 `false`。开启后逐章写作经 `hermes chat --resume`
  接续上一章的**子会话**；只有成功的章节才把 session 传下去，续接失败自动降级为全新会话。

### 桥层（Obsidian 联动）
- 目录清单配置化（`obsidian.vault_dirs` / `skip_dirs`），`obsidian_bridge` 与 `kb_index`
  **同源**（此前两处各写一份且已漂移）。
- 修 `has_obsidian_entry` **恒假**（`character_dirs` 留空时从未真跑过该分支）。
- 新增 `obsidian_bridge.py diff`：vault ↔ 设定集**内容级对账**（缺失/新增/改名/丢 locked 一次报全）。

### 发布树卫生
- 新增 `scripts/leak_scan.py` 泄露门禁（**fail-closed**：词表缺失/为空/条目不足 → 拒绝放行）
  + CI 门禁；发布树**虚构化**（示例宇宙统一）。修掉 `git ls-files` 转义非 ASCII 文件名
  导致中文名文件**从未被扫过**的漏洞（实测少报约 60 处）。

---

## [0.4.0] — 2026-10-01 locked 违例落地 · 多书归档范围修正 · 模型切换通道自检 · CI

### 🔴 GUI 实测反馈三修（2026-10-01，装上安装包实测后）

**① 设置页「在文件夹中显示」点了没反应 → 用户卡在「根本无法配置 Key」**
2026-09-19 审查 S7 只砍了「用外部编辑器打开 `.env`」的出口，但 `reveal-in-folder` 把路径
解析成 `path.resolve(ROOT, ".env")` —— 打包态 `ROOT` 是**安装目录里的 payload**
（随包只读，且 extraResources 里没有 `data/`、没有 `.env`），而 `.env` 与全部用户数据都在
workspace（`%APPDATA%\绒花墨坊\workspace`）。于是恒返回「文件不存在」；
前端**又不检查返回值**、照样提示「已在文件夹中定位」→ 用户看到的就是「点了没反应」。
- 同源第二处 bug：`pathAllowed` 按 `ROOT` 前缀 `slice` 算相对路径，workspace 来的路径
  会切出一段垃圾 → **所有**指向用户数据的请求都被判「不在白名单」——关于弹窗的「打开」
  按钮、产物速览同样打不开。
- 修：新增 `resolveExisting()`（**workspace 优先 → 代码根兜底**），四个文件类 IPC
  （`read-preview` / `open-artifact` / `open-file` / `reveal-in-folder`）统一走它；
  `pathAllowed` 按两个根各自计算相对路径；前端检查返回值，失败时给出 `.env` 真实路径。
- 设置页文案改成明确指引：**配置 Key 直接点「填入 Key」**（后端写盘、立即生效、不回显），
  不必手动找文件 —— `.env` 仍刻意不提供「一键用外部编辑器打开」。

**② 暗色主题下输入框 / 卡片文字看不清**
`style.css` 里输入框、卡片、侧栏、流式输出写死 `background:#fff`（以及 `#fbfbfd` /
`#f6f6fa` / `#eae6f7` 等近白），而文字是 `var(--ink)` —— 暗色主题下 `--ink` 是**浅色**，
于是「浅底浅字」。更隐蔽的是暗色覆盖**只写了 `.theme-dark` 一套**，三套
`night-*`（暗夜蓝 / 绿 / 暖）完全裸奔，连顶栏都是 `rgba(255,255,255,.72)`。
- 修：引入 `--field / --field-inset / --field-dim / --field-hover / --field-on /
  --field-head / --field-code / --topbar` 与 `--ink-{ok,bad,warn,mute,accent}`，
  10 套主题全部补值；新增 `.is-dark` 公共覆盖（`setTheme()` 自动挂类，新增暗色主题
  只需进 `DARK_THEMES`）；顺手补上**从未定义过**却已被 12 处引用的 `--muted-foreground`。
- **测试也补了漏检**：`test_gui_api_contract` 的主题正则写的是 `[a-z]+`，
  匹配不到带连字符的 `night-blue` / `night-green` / `night-warm` →
  这三套主题**从未被这条检查覆盖**，裸奔也没人发现。现改为 `[a-z-]+` 并断言"解析到全部 10 套"。
- 新增 `tests/unit/test_ui_theme_and_paths.py`（67 断言）：除静态判据外，用
  **WCAG 对比度真实计算**断言「每套主题 控件底 × 正文色 ≥ 4.5」，
  并用 node 跑**真实函数体**做「payload + workspace」双根路径反证。

**③ 「检查更新」像是没了 / 点了没反应**
两个独立问题叠在一起：
- **`updater:check` 把 `checkForUpdates()` 的返回对象直接过 IPC** —— 里面含
  `cancellationToken`（EventEmitter 实例）等**不可结构化克隆**的字段 → invoke 直接 reject。
  前端当时没有 `try/catch` → `checkingUpdate` 永远停在 `true`、按钮保持 disabled →
  表现就是「点了没反应 / 功能没了」。现在主进程只回传可序列化字段
  （当前版本 / 目标版本 / 日期），前端 `try/finally` 兜底。
- **「关于」弹窗里只有「下载新版本」外链**，没有检查更新入口 → 用户以为功能被砍。
  现在两处都有；「下载新版本」也改为常驻（便携版 / 开发模式下自动更新本就不可用，
  手动通道不该藏在条件下），并按状态说明原因。
- ⚠️ **要让「检查更新」真有用，必须把安装包上传到 GitHub Release**（`latest.yml` 是
  electron-updater 的唯一数据源）。仓库 release 尚未上传时，检查结果会是
  「暂无更新（当前已是最新版本）」—— 这是**如实**的，不是坏了。

### 🔴 多书归档范围修正（`switch_book.py`）—— 换书不再丢配置与素材卡
- **问题**：`--archive` 只搬 `data/` 下 8 项，`config/` 与 `materials/` **完全不归档** ——
  「归档成功」的代价是丢掉 `project.yaml`（书名/类型/章节数）与全部素材卡，下次才发现。
- **修复**：两者纳入范围。`system.yaml` / `system.local.yaml` 属**应用级**（描述"这台机器怎么跑"），
  归档后立即复制回工作区 —— 否则归档一完成工作区就缺 `config/system.yaml`，界面直接起不来。
- **恢复语义 = 逐项合并**（非整体替换）：书档里没有的文件不会删掉工作区现有那份；
  老格式归档（无 `config/`）恢复也不会毁掉工作区配置。
- **顺带修一个真回归**：归档后工作区没有 `project.yaml`，而 `/project/create` 走的是
  **定向改写**（读原文件再改）→ `FileNotFoundError` 500。现在归档后留一份**空白骨架**
  （结构保留、book 值清空、技术参数如 `word_template` 不动）。

### 🟢 `locked` 不可违逆：从提示词层落到确定性检查
- `check_consistency.locked_violations` **自 2026-09-21 起恒为空列表**，CLI 却打印「违例 0 个」——
  看起来像通过。现在接上三条**结构性**判据：`missing`（条目在最终设定集里消失）/
  `lock_lost`（条目还在但锁定标记丢了 → 角色卡不再警告）/ `renamed`（同 vault 路径换了名字）。
- **语义冲突依旧不碰**（"本章事实是否与 locked 设定矛盾"需要世界模型，硬做只会变噪音），输出里明说。
- **不生效就说"没查"**：vault 无 locked 条目、或设定集尚未生成时回报 `checked=false` + 原因。
  一个永远说"没问题"的检查器比没有检查器更危险 —— 这个模块此前正是栽在这里。
- 新增 `python scripts/obsidian_bridge.py locked [--setting 路径]`（零 token）。

### 🔵 `nfctl model-check`：换模型 / 换供应商前的通道自检
- 一次列清所有会挡人的点：配置完整性、**Key 脱敏回显（前 3 后 4）**、各角色模型是否在该
  provider 的 `available_models` 内、**fallback 是否跨供应商**（fallback 复用当前 provider 的
  端点与 Key，放别家模型名 = 一跳就 404 并吃掉整条链）、白名单覆盖面。`--live` 才发最小请求。
- **GUI 侧同时修 3 个会绊住换模型的坑**（`App.vue`）：
  ① 模型下拉只取 providers 里**第一个**的 `available_models` → 新增供应商后它的模型根本不进下拉；
  ② 「↻ 同步服务商模型」**硬编码 tokenhub**，还会把 provider 下拉重置成只剩 tokenhub → 切过去就选不回来；
  ③ 切供应商后后端不校验模型是否还在清单里 → 现在返回 warning，GUI 提示"请同时改选模型"。

### 🟣 美团 LongCat（龙猫）通道预置
- `config/system.yaml` 新增 `providers.longcat`（OpenAI 兼容，`LongCat-2.0` / `LongCat-2.5-Preview`）。
  **代码零改动** —— 换供应商只改配置（`llm_client.make_client` 全从配置取）。
- ⚠️ 两处待实测（官方文档自相矛盾）：`base_url` 结尾要不要 `/v1`、`/models` 端点是否支持。
  `nfctl model-check longcat --live` 一跑就知道。
- ⚠️ **成本口径**：系统目前只有"token × 刊例价"一套算法，**不区分按量 / 订阅套餐 / 免费额度**。
  走免费额度或包月时界面上的费用是**虚拟值**，300 元熔断会按虚拟值误触发。

### 🟡 CI（GitHub Actions，`.github/workflows/ci.yml`）
- 每次 push / PR 自动跑质量门（pyflakes，未定义名零容忍）+ 全量测试（`nfctl test`）。
- 刻意**不跑 e2e**（需 playwright 浏览器 + 4 个空闲端口，CI 上不稳）—— 假红比没有 CI 更糟，
  一旦"红灯是正常的"成为共识，真红也没人看。

### 🟠 长跑保护：不让"坏掉了还一路跑完"留下满盘废稿
- **stage4 连续失败止损**（`gates.stage4_max_consecutive_failures`，默认 3，0=关）：
  原实现单章失败只 `continue`，100 章全废也只在最后报一句"有 100 章失败"—— 废稿已全在盘上。
  现在**连续** N 章失败即判系统性问题并提前停止（单章偶发失败仍照常继续）。
- **阶段内预算熔断**（stage4/5/6）：orchestrator 只在**阶段之间**查预算，而 stage4 一章就能
  烧掉几元 —— 300 元限额对"一次跑 100 章"这种最该保护的情形**恰好失效**。现在章/卷/批之间
  也查，超限即停并把 `budget.paused` 写入 progress。
- **续跑判据不再只看 `exists()`**（stage5 的 `checked`、stage6 的 `refined`）：
  空壳/残稿会被 `exists()` 判成"已完成"→ **永久跳过、重跑也救不回来**，再被下游
  当合格稿处理。2026-09-29 件7 的空壳事故正是这样一路穿过去的（stage5 那一次还
  多穿了一层：`missing` 兜底也判 `exists()`，空壳连"从 raw 复制"都触发不了）。
  现在共用 `utils.verify_chapter.is_usable_output`（**判据只有一份**）。
- 新增 `tests/unit/test_stage4_stoploss.py`（16 断言）与
  `tests/unit/test_resume_output_judgement.py`（13 断言）：判据是**行为级**的 ——
  前者看"到底跑了几章"，后者还断言**调用点真的用了新判据**（防"修好了又漂回去"）。

### 🔴 三处真 bug（由本轮新增的检查现场抓到）
- **`proofread.py` 的 `load_template("stage5_proofread")` 漏了 `.md`** → 文件永远找不到
  → 异常被 `except` 吞掉 → **永远走内置兜底**。结果是：用户在 GUI 里编辑
  `prompts/stage5_proofread.md` **完全不生效**，界面上没有任何提示。顺带发现
  `load_template` 返回的是 `(meta, body)` **元组**，原代码直接当字符串用 ——
  即便文件存在也会在拼接时 TypeError。
- **校对提示词的 `{{SETTING}}` / `{{CHAPTERS}}` 从未被填充**：调用方既没传 variables、
  又在外面手工拼了一遍标题与正文 —— 占位符于是原样发给模型。
- **多书归档带走 `project.yaml` 后 `/project/create` 500**（见上方归档小节）。

### 🟢 GUI 易用性
- **API Key 改成 GUI 内写入**：主进程按 2026-09-19 审查 S7 把 `.env` 排除在外部打开白名单外
  （程序不该代为用外部编辑器打开明文密钥），但当时**只砍了出口、没给入口** —— 界面上那个
  「打开 .env 填入 Key」按钮点下去必然报「路径不在白名单」。现在改为 `POST /env/set`
  （键名白名单 = 各 provider 的 `api_key_env`；值不回显、不落日志，响应只给前 3 后 4；
  写前备份 `.env.bak`；写后立即刷新进程环境，**无需重启**）。
- **`GET /env/open` 的模板改为按当前 providers 动态生成**（原先硬编码 `TOKENHUB_API_KEY`，
  新增龙猫后用户打开 `.env` 找不到该填哪个键名）。
- **Agent 模式开关从「设置」提到流水线首页**：高频且影响权限语义，藏起来等于没有。
- **提示词模板可回滚**：新增 `POST /prompts/restore`（从 `prompts/history/` 选版本），
  编辑器里直接给下拉 + 按钮。回滚走 `save_prompt` 同一路径 → 备份链保持连续（手工拿文件
  管理器覆盖的话，"改坏的那版"不进备份链，出问题时连对照物都没有）。
- **`save_prompt` 拒绝保存空内容**（空模板 = 该阶段收到空提示词 → 直接跑出废稿）。

### 🟡 提示词防线（回答"要不要在源码里再留一套默认预设"）
**不另留一套**：payload 里的 `prompts/` 本身就是预设，Electron 升级会 force 覆盖回来；
再加一份源码内副本只会引入"到底哪套生效"的新歧义。真正缺的是**恢复入口**与**体检**，
本轮补齐 —— `load_template` 缺文件时给可执行的恢复方式；填充后残留占位符告警；
`nfctl check` 从代码里的调用点反推必需清单做体检（缺文件 / 空文件 / 调用名漏 `.md` 一律阻塞）。

### 🔴 第二轮系统审查（阶段状态机 / API 与前端 / 底层工具）—— 11 处缺陷
- **前端「全自动」实际只跑了阶段 1**：`/stage/1/run` 没传 `only_stage:false`，而后端
  把 URL 里的阶段号默认当成"只跑这一阶段" → 界面提示"已提交全流程运行"，实际停在
  阶段 1。**典型的假成功。**
- **orchestrator 重试成功后跳过后置钩子**：后置动作（快照 / 素材体检 / 自动重写 /
  **审稿门** / 校对）原先写在「首次就成功」的分支里，"首次失败 → 自动重试成功"这条
  路径会**整块跳过** —— 其中 `review_after_stage4` 本该停下来等人工审稿，被跳过后
  直接冲进 stage5/6。而 `auto_retry` 默认开，这是常见路径而非边角情况。
  已抽成 `_post_stage()`，两条路径都调用。
- **成本把缓存 token 双重计价**：`cached_tokens` 是 `prompt_tokens` 的**子集**，
  旧算法 `tokens_in * 输入价 + cache_read * 缓存价` 把命中部分算了两遍。实测缓存
  占比可达 ~95% → 输入成本高估近一倍 → **熔断提前触发**（本该跑完的书被拦腰截断）。
- **`is_chapter_complete` 把以「……」结尾的完整章判成截断**：省略号是中文小说对话与
  留白的**合法结尾**，误判的代价不是"多跑一次"而是**反复重写**（每轮都当半成品，
  模型保持同样结尾就永远完不成，钱一直烧）。改为只认真正的停顿符；真截断由
  `finish_reason == "length"` 的产出契约判定。
- **`reject.py --stage 3` 是空操作**：清理列表漏了 stage3 **自己的产物**
  `data/outline/chapters` —— 旧逐章大纲一个没删，而 stage3 的断点判据那时只看
  `exists()` → 重跑直接判"全部已存在"并 `mark_stage_done`。用户以为重做了，实际
  喂给 stage4 的还是那批被打回的大纲。同时给 2/3/4 补上 `data/summaries`（滚动摘要
  不清会新旧叠在一起，旧情节被当上下文注入写作）。
- **`merge_book` 只取第一个非空目录**：`refined/` 里残留 1 个文件就只合并 1 章，
  `checked/raw` 里其它章**全被忽略** —— 成品就此定稿，且 stage7 会 `mark_done`，
  断点续跑再也不会补。改为逐章按优先级取（refined > checked > raw），并在调用方
  校验合并章数（只判"docx 非空"是拦不住残卷的）。
- **`snapshot.restore` 单项失败仍返回 True**：界面/MCP 显示"已恢复"，磁盘上却是
  新旧混杂，用户不会再补救。改为以"全部成功"为成功条件。
- **`ProgressManager` 对「合法 JSON 但顶层不是对象」直接崩**（`[]` / `"x"` / `123`
  都能 `json.loads` 成功，随后 `data.items()` AttributeError 穿透出去把流水线打挂）。
  与语法错误同样处置：告警 + 隔离原件 + 降级。
- **`build_state` 缺顶层 `agent_mode`** → GUI 开关永远显示"已关闭"，哪怕
  `system.yaml` 里是 true（用户完全看不出真实权限状态）。**把 Agent 模式开关提到
  首页后，这个问题会被放大**（首页常驻显示错误状态）。
- **MCP `nf_run_stage` 声明支持阶段 8**，而后端 `/stage/{n}/run` 只收 1-7 →
  Agent 照契约调用必然撞 400。改为 1-7 并注明阶段 8 走 `nf_export_markdown`。
- **stage3 断点判据只看 `exists()`** → 复用 `check_chapter_outline`（判据只有一份）。
- **`do_GET` 无兜底 → handler 异常让请求「挂起」**：`_dom()` 只展开结果、不捕获异常，
  而 GET 分发链没有 try（POST 侧一直有）→ 任何 handler 抛的异常都会冒泡出 `do_GET`，
  socketserver **直接断连**：客户端既拿不到 400 也拿不到 500，只表现为"请求挂起 /
  连接被重置"，排查时看不到任何信息。实测触发点 `GET /chapters/paragraphs?n=abc`。
  现在 `do_GET` 是一层兜底包装（真链搬到 `_do_GET_raw`）—— 实测该请求从"挂起"变成 500。
- **`nf_api_domains/refine.py` 两处裸相对路径**：`--root <书B>` 时进程 CWD 未必跟着换，
  裸相对路径会**静默读到另一个项目**的章节做对比。改经 `api.ROOT`。

新增 `tests/unit/test_audit_fixes_20261001.py`（22 断言）：数值类（计价口径、截断
判据）用真实断言；结构性（reject 清理范围 / build_state 字段 / MCP schema /
orchestrator 钩子调用点 / 前端 only_stage 传参）用**源码断言**防"修好了又漂回去"。

---

## [Unreleased] — 2026-09-30 MCP stdio 垫片补位 · 定价批量导入 · `--root` 路径纪律清完

### 🔴 MCP stdio 垫片（`scripts/nf_mcp_stdio_bridge.py`，执行单 ⑤前置步0）
- **问题**：`nf_mcp.py` 跑的是「TCP + 换行分帧的裸 JSON-RPC」，**不是**标准 MCP 传输 ——
  标准客户端（Hermes / Claude Code / Cline）只会在本地 spawn 进程、用 stdio 说话，
  **直连不上 8766**。此前仓库里只有一句「中间必须有 stdio 垫片」，垫片本身不存在。
- **交付**：stdio ↔ 8766 双向透传。逐行搬运之外，真正的价值在「搬不动时怎么说话」：
  8766 连不上时返回 **-32002 + 层号 + 可执行启动指引**（沿用原交付物已实测的错误码约定），
  `id` 保真，客户端能把它当该请求的正常响应处理。
- **关键纪律**：stdout 只走协议（日志一律 stderr）；惰性连接（8766 后起也能用）；
  不解析不改写 payload；分帧缓冲留在实例上（一次 `recv` 常带回多行，就地拆行会发**半行错误帧**）。
- **反向验证**：破坏「拒连错误码」与「连接目标端口」两处 → 自检分别打红。
  过程中还揪出一段**假绿**代码（「发送失败后立即重连再试一次」——socket 半开时 `sendall`
  往往成功，该分支几乎不触发，改坏它自检照样全绿）→ **删掉**，不留自以为是的安全网。

### 🟢 定价批量导入（`POST /costs/rates/import`）
- 定价面板此前只能**逐行手工编辑**，缺"整表粘贴"。现支持三种写法自动识别：
  JSON / **Python 字面量（RATES 块整段复制）** / 表格行（分隔符任意，吃货币符·千分位·
  `元 / 百万 tokens` 尾巴·注释·表头·`免费`）。
- **两段式**：`confirm=false` 只预览（零写入）/ `true` 才落盘；有解析错误**一律拒绝写入**（400）。
- **合并语义 upsert**：只覆盖同名条目，不删其他；缺 `cache_read` **沿用现有值 + 告警**
  （静默置 0 等于把缓存命中当不花钱 → 账单偏乐观）。落盘前自动留 `.bak`。
- **跳过任何一行都留痕**：识别不了进 warnings、语义错误进 errors，均带行号。

### 🟠 `--root` 路径纪律清完 + 可执行护栏
- 12 处相对数据路径改经 `ROOT`（`ProgressManager` ×4、`RunDB` ×2、审稿/校对报告路径、
  `write_task`、`kb_index`、`add_comment_to_finding`、域模块 `outline.py`）。
- 新增 **`_set_root()`**：改 `ROOT` 的唯一入口，同时刷新 `GLOBAL`/`HISTORY_DIR` ——
  这类**模块级派生常量在 import 时固化**，只 `global ROOT` 会让它们继续指旧项目
  （"拼了、但拼的是旧 ROOT"，比裸相对路径更阴）。
- 护栏 `tests/unit/test_root_path_discipline.py`：AST 判据（禁「相对数据路径裸当函数实参」，
  只查调用实参故常量表不误报）+ `global ROOT` 只许在 `_set_root` + 功能反证（读到夹具书名）。

### 🛡️ 发版保障：把「写在文档里的纪律」变成一条命令
- **`nfctl release-check`**：质量门 → 全量测试 → MCP 真机握手 → e2e 视觉验收 → 打包产物核验，
  一条命令跑完；任一 FAIL → exit 1，**SKIP 不算失败**（缺依赖 / 缺外部服务 = 未执行）。
  - e2e 那步**自起 4 个服务、结束时只杀自己起的 PID**；端口被占则 SKIP
    （不抢端口、更不按进程名杀别人的东西）。
  - **产物核验**的判据是「payload 里 4 个关键脚本的 sha256 必须与工作区一致」——
    专抓「改了代码却没重打包」。**首次运行就抓到一例真问题**（垫片文件头改过、包没重打）。
- **`scripts/nf_mcp_handshake_check.py`**：真 `nf_api` + 真 `nf_mcp` + **官方 MCP SDK** 全链路
  握手自检（自起 18765/18766，不碰用户端口；缺 `mcp` SDK 则干净跳过）。
- **`requirements-dev.txt`** 补登 `playwright` 与 `mcp`：此前它们只装在某台机器的 `.venv` 里，
  干净克隆上这两个闸门会**静默跳过** —— 那等于闸门不存在。
- **`docs/mcp-connect.md`**：MCP 接入三步 + 三级验证方法 + 排错对照表（哪一层坏了、去修谁）。

### 📌 文档与实测对齐
- `AGENTS.md` MCP 章节：工具数 **20 → 22**（件5 加了 `nf_get_remedy` / `nf_restore_snapshot`
  后一直没同步），并补上「标准客户端须经 stdio 垫片」与三层分层口径。
- `nf_mcp.py` 文件头：点明垫片路径与用法。
- 两处新增自检：`test_rates_import`（53 断言）、`test_mcp_stdio_bridge`（15 断言）、
  `test_root_path_discipline`（9 断言）。

---


## [Unreleased] — 2026-09-29 ①开工执行单 v2.2 落地（六件 + 随行件）

依据 `shared/quickfix-plan-20260928.md`（执行单 v2.2）与
`shared/report-20260929/汇总报告-20260929.md`（09-29 晨三方对账）。统一背景：
本轮修的都是「**门禁失守还盖绿灯**」类缺陷 —— 产物存在 ≠ 产物合格，
报错存在 ≠ 报错说清了哪一层坏了。

### 🔴 件2 解析层容错（`utils/validator.py`、`utils/llm_client.py`）
- **剥离 `<think>` 内嵌思考块**：R1 系网关会把思考直接内嵌进 `content`，全库原先
  grep 零剥离 → 思考残片会随正文落进产物。解析层与 answer 层各剥一道。
- **断裂 JSON 兜底**：`max_tokens` 截断或模型中途收尾时，用括号配对扫描补全未闭合的
  `}`/`]`（并去尾部悬空 `,`/`:`）—— 09-28 冒烟的「审稿 JSON 断裂」为实测事故。
- **从 reasoning_content 捞回 JSON**：`_salvage_answer` 原先只认 `===FILE:` 协议块，
  审稿/校对的结构化结果整段落进 `reasoning_content` 时捞不回（审稿断裂根因）。
  新增分支要求「**真能 json.loads 通过**」，故纯散文思考仍被丢弃 ——
  2026-09-23「思考残片进小说正文」的修复不被破坏。
- **失败不抛裸异常，并回报原文偏移**（`LAST_ERROR` + 一行 WARN）。

### 🟠 件3 outliner 锚点数硬约束（`prompts/stage2_global_outline.md`、`stage2_outline.py`）
- 提示词写入「每节点锚点数下限」硬约束（≥1 场景地点 + ≥1 具体角色行动 + ≥1 具体事件 + ≥30 字）。
- 新增产出侧校验器 `check_outline_anchors`：THIN 条目 > 1 即判不合格打回。
  判据**复用** `outline_review`（不另写一套打分）；结构校验 `check_global_outline`
  保持纯净（GUI 保存/精修共用它，不应因空泛被拦）。

### 🟠 件4 质量验收闸门化（`orchestrator.py`、`config/system.yaml`）
- 全流程收尾**强制**跑 `quality_checklist.py`，非零退出即 🔴 阻断完成状态（exit 1）。
  新增 `gates.quality_gate`（默认 true）。仅在**全流程**收尾生效；
  `--stage N` 单阶段重跑不拦（避免拿整书清单误伤单阶段）。
- 闸门自身异常时**大声告警但放行**（已完成的书不该因一次工具故障被判不合格）。

### 🟠 件5 MCP 白名单补两把钥匙（`nf_mcp.py`、`snapshot.py`）
- 新增 `nf_get_remedy`（HTTP `GET /remedy`，接数据一致性/孤儿文件诊断）。
- 新增 `nf_restore_snapshot`（本进程内复用 `snapshot.restore_snapshot`）：
  两段式（**默认只预览**，`confirm=true` 才执行），并保留「恢复前自动打
  `pre_restore` 快照」的折返守卫 —— 回滚是破坏性操作，判据只允许有一份实现。
- 新增 `snapshot.snapshot_ids()` 公共只读助手，供 CLI 与 MCP 共用（消除重复判据）。

### 🟠 件6 服务未启动指引分层（`nf_mcp.py`、`README.md`）
- 8765 不可达时返回**带层号**的干净报错（8766 = MCP 传输层 / 8765 = 服务本体）
  + 可执行启动命令；企业代理/沙箱把「连不上」表现成网关 5xx 时同一份文案。
- 修正 `nf_mcp.py` 文件头**虚标**：原文称「compatible with any MCP client that
  supports TCP/HTTP transport」，实为 TCP 换行分帧**非标**传输，标准客户端需 stdio 垫片。

### 🟠 件7 stage5 输入构造修复（`stage5_check.py`、`prompts/stage5_check.md`）
- 批次列表由**裸文件名**改为**绝对路径**（`f.resolve()`）：原先按 CWD 找不到 →
  章节从未进入 prompt → stage5 产出空壳（冒烟实测输入仅 2,048 token，真内联应 1 万+）。
- 模板设定集行补 `- ` 前缀：解析器只认列表行，该行原先从未被内联。
- 连带翻案：stage5/6「空壳」不是模型能力问题，**kimi-k2.6 换型决策悬置**至修复后复跑。

### 🟡 随行件
- **随行1** `orchestrator.py` 的裸 `import time` 上移模块头（行为零 diff）。
- **随行2** `cost_tracker.RATES` 补 `kimi-k2.6`（当前 7 角色主力，原先走回退默认价 +
  WARN，账单绝对值不可信；**数值为占位，拿到 TokenHub 刊例价后需更新**）；
  `system.yaml` fallback 摘除 `glm-5`（2026-10-09 下线 —— `_post_chat` 对 400/404
  直接 raise，死模型一跳会吃掉链上后续所有兜底）。
- **随行3** `segment_requests` 的「仅处理第 i/N 个」指令**移尾**（原先头插在 user 消息
  最前，N 个子请求共享 95%+ 内容却在首字符分叉 → 前缀缓存全灭）。
- **随行5** `tests/e2e/e2e_ux_verify.py`（64 断言真机视觉验收）列入 **v0.3.2 发版前必跑**。

### 🧪 新增自检
`tests/unit/test_json_salvage.py`（件2）· `test_outline_anchor_gate.py`（件3）·
`test_quality_gate.py`（件4）· `test_mcp_remedy_snapshot.py`（件5/6）·
`test_stage5_inputs.py`（件7，含反证：复刻修复前构造必须复现故障）。

---

## [Unreleased] — 2026-09-19 工程审查修复

依据 `deliverables/engineering-assurance/comprehensive-audit-novelforge-2026-09-19.md`
（8 项严重 / 12 项高危 / 14 项中 / 8 项低）执行止血与保障体系建设。

### 🔴 修复：默认配置下必崩的活缺陷

- **S1** `orchestrator.py` 重试路径 `datetime` 未导入 → 必然 `NameError`
  - `auto_retry` 默认 `true`，任一阶段失败即触发；且崩溃点无异常保护，
    `db.finish_run()` 永不执行 → `runs` 表残留 `status='running'` 脏行。
  - 修复：补 `from datetime import datetime`；重试循环整体包 `try/except`；
    编排层引入 `_finalize()` 统一收尾 + `finally` 幂等补偿。
- **S2** `nf_api.py` 流式分支引用未定义的 `only_stage` → GUI 跑流水线必崩
  - 先回 `202 {job_id, stream:true}` 再在后台线程抛错 → GUI 显示「正在流式输出」
    却永远等不到 token（典型静默失败）。
  - 修复：**消除流式/非流式平行分支** —— 抽出 `_rc_to_result()` 共用退出码翻译 +
    `act_run_stage()` / `act_run_stage_streamed()` 两个工厂。
- **S2+（本轮新发现，审查报告未覆盖）** `nf_api.py` 的 `/kb/search` 与 `/kb/build`
  引用未导入的 `get_vault_path` → 端点一旦被调用必 `NameError`
  - 长期存活原因：全部 HTTP 契约测试**无任何 kb 用例**（零覆盖）。
  - 修复：补齐导入 + vault 未配置时给可行动 400；新增 kb 端点 HTTP 契约用例。

### 🔴 修复：安全与配置

- **S3** `config/system.yaml` / `config/obsidian_templates.yaml` 硬编码用户本机
  绝对路径（含已废弃命名的漏网残留），并散落在 `obsidian_bridge` /
  `kb_index` / `obsidian_postprocess` / `voice_to_docx` 的默认值中。
  - 修复：改为占位符 + 项目内默认沙盒（`data/state/obsidian_sandbox`）；
    `get_vault_path()` 未配置返回 `None`（**不再返回 `Path("")`** ——
    后者的 `str()` 是 `"."`，会让「是否已配置」的判断永远为真）。
  - 顺带移除 14 个测试脚本中硬编码的本机仓库路径，统一改为 `__file__` 解析。
- **S7** `console/main/index.js` 把 `.env` 放进「外部打开」白名单 → 明文密钥可被
  外部程序直接打开。
  - 修复：移出白名单；新增 `reveal-in-folder` IPC（只定位不打开），
    GUI「打开 .env」改为在文件管理器中显示。
- **S8** 模型准入「**约束数据存在、回路从未接通**」：白名单数据（`available_models` /
  `fetched_models.json` 的 `_manual`）确实存在且被多处读写，但**全部消费点都是
  展示或缓存维护**，不存在任何判定回路；`/models/switch` 连格式校验都没有。
  - 修复：新增 `scripts/utils/model_registry.py` 作为纪律的唯一实现点，
    两级校验（① 格式合法性 ② 白名单归属）+ `strict=false` 逃生门（回报未校验）；
    `/models/switch`、`/models/add` 均接入。

### 🔴 修复：工程保障体系（比单缺陷更根本）

- **B1** `.github/` 是完全空目录 —— **本项目从未有过 CI**。
  且 `.gitignore` 的 `*.yml` 会静默吞掉未来任何 workflow 文件
  （`git check-ignore -v` exit=0，推不上仓库且不报错）。
  - 修复：`.gitignore` 加 `!.github/workflows/*.yml` 白名单；
    新增 `.github/workflows/ci.yml`（pyflakes 门禁 + 语法检查 + 单元测试）。
- **B2** `Temp/` 被 gitignore，而它是**事实上的测试目录**（67 py + 129 截图）
  → fresh clone 后测试全丢，`AGENTS.md` 引用的 20+ 测试路径全部失效。
  - 修复：新建 `tests/{unit,http,e2e}/` 并迁入 31 个测试脚本，纳入 Git；
    `AGENTS.md` 引用同步校正。
- **B3** 零 Linter；以 `py_compile` 当质量门禁（纯语法检查，对「名字写错」完全无效）。
  - 修复：新增 `scripts/quality_gate.py`（pyflakes 封装，**未定义名零容忍**）。
    本轮 4 个活缺陷全部由它命中。

### 🟠 其他改进

- `db.py` 新增 `finish_run_if_running()`：编排级幂等补偿，把崩溃残留的
  `status='running'` 收敛为 `crashed`；读侧「绕过 running 过滤」的容错**保留**
  作为深度防御第二层。
- `/models/switch` 校验失败时回报 `suggestions` 与 `registered_count`，
  便于用户纠错。

### ✅ 测试

- 新增 4 个回归用例（共 **87 断言**）：
  - `tests/unit/test_orchestrator_retry.py`（13）— S1 回归
  - `tests/unit/test_stream_stage.py`（18）— S2 回归
  - `tests/unit/test_config_and_models.py`（29）— S3/S8 回归
  - `tests/http/test_kb_and_models_api_http.py`（27）— kb + 模型端点契约
- 每个回归用例均经**反向验证**（临时还原缺陷 → 用例必须失败 → 恢复），
  确认不是「永远为绿」的空用例。
- 既有测试套件全绿：65 条新端点 HTTP 断言、27 条 GUI↔API 契约断言、
  44 条碎片聚类断言等。

---

## 第二轮：失败路径覆盖 + 两个新活缺陷（2026-09-19 晚）

第一轮把「主路径」修干净了，但报告 H5 指出的核心问题仍未解决：
**全仓 22 个 `result["exit_code"] != 0` 分支一次都没被测试走到过**
（`FakeClient` 恒 `exit_code=0`）。这一轮是正面回应 ——
**主动把系统打坏，看它是否按承诺的方式坏。**

### 🔴 新增缺陷 S9：默认全流程最后一站必崩

**发现方式**：写 F3（预算熔断）用例时，用例本身报 `KeyError: '8'`。
这个异常与预算毫无关系 —— 顺着查下去才发现是真缺陷。

| 项 | 内容 |
|---|---|
| 现象 | 默认全流程（`from_stage=1`、无 `--stage`）跑到阶段 7 完成后，进入阶段 8 时抛 `KeyError: '8'` |
| 精确位置 | `orchestrator.py:213` 的断点判断 `progress.stage_status(8)` |
| 根因 | `progress_manager.STAGE_KEYS` 只声明 `("1".."7")`，而 orchestrator 遍历 `range(from_stage, 9)` 会取到阶段 8；`STAGES` 表里也确实有第 8 项（Markdown 分卷导出） |
| 后果 | 异常穿透 `run()`，由 `finally` 兜底把 `runs` 标为 `crashed`（退出码既不是 0/1/2，也不是 3/4）→ **正常跑完的书被记成崩溃** |
| 为什么一直没暴露 | 全流程因各式原因多在早期阶段暂停，从未有人跑到阶段 8；且测试全用 `only_stage`，绕开了这条路径 |

**修法（三层）**：
1. `STAGE_KEYS` 补齐到 `"8"`（对齐编排遍历范围）；
2. 新增 `_ensure_stage()` 纵深防御 —— 阶段键缺失时**自动登记**而非 KeyError，
   把「配置失同步」从「全流程崩溃」降级为「多一个键」；
   16 处直接下标 `self.data["stages"][str(stage)]` 全部改走此方法；
3. `reject.py` 的下游重置范围 `range(stage+1, 8)` → `range(stage+1, 9)`，
   否则打回后阶段 8 残留 `done`，断点逻辑会误判「已完成」而跳过。

### 🔴 新增缺陷 S10：预算熔断的「最后一站漏洞」

| 项 | 内容 |
|---|---|
| 现象 | 最后一个阶段（阶段 8）成功且已超预算时，退出码为 0、`runs.status='done'`、GUI 显示成功 |
| 精确位置 | `orchestrator.py` 阶段循环末尾：`state, spent = cost.status(run_id)` 只 `print` 一句，**从不据此停机** |
| 根因 | 死代码 —— 计算了 `state` 却没有任何 `if state == "pause"` 分支消费它 |
| 后果 | 熔断只在「下一阶段启动前 / 重试前」生效；**没有下一阶段时，超预算被静默吞掉**，最终宣告"全部完成" |
| 性质 | 典型的**静默失败** —— 用户看到成功，账已超支 |

**修法**：阶段末尾的检查与阶段起始处的熔断保持同一语义（置 `budget.paused`、`_finalize("paused", 2)`）。

### ✅ 失败路径测试基建

- **新增 `scripts/utils/failing_client.py`**：可控失败的假客户端（生产代码零改动）。
  - `FailingClient(fail_on=..., fail_times=..., raise_exc=...)` —— 支持「全失败 / 指定阶段失败 / 前 N 次失败 / 抛异常而非返回非零码」四种策略；
  - `ExpensiveClient` —— 用于「成功但烧钱」的熔断场景（与「失败」正交）；
  - 设计要点：`exit_code != 0` 时**不写任何产物**（真实子会话崩了正是不写），否则测的就不是失败路径。
- **新增 `SEED_VERSION` 升级机制**（`console/main/index.js`）：
  此前 `seedWorkspace()` 在 `.seeded` 存在时直接 `return` ——
  **「只在首启播种」实际变成了「永远不再更新」**。桌面端用户升级 App 后，
  workspace 里跑的还是旧版 `scripts/`、`prompts/`，而 `config/` 是新的
  → 新配置撞旧代码，失败且无法从 UI 诊断。
  现在按版本号刷新代码目录（`force` 覆盖），**不动用户 `data/`**，
  `config/` 只补缺失文件、绝不覆盖用户填的书名/路径。

### ✅ 测试

- **新增 `tests/unit/test_failure_paths.py`（69 断言）**，覆盖 F1~F8 + S9/S10：
  - F1 重试计数真实递增（首次 + 2 轮 = 3 次调用，`retry_count=2`）
  - F2 异常不外泄（`run()` 返回而非抛；`runs` 无 `running` 脏行）
  - F3 / F3b / F3c 三处熔断窗口分别验证（阶段末尾 / 重试循环内 / 末阶段）
  - F4 停止请求在阶段启动前即生效（零额外 LLM 调用）
  - F5 stage4 逐章失败精确隔离（第 1 章失败不阻断第 2 章；重跑后失败清单清空）
  - F6 静态门禁 + **反向验证**（注入未定义名必须被检出，证明确实不是空门禁）
  - F7 升级刷新（代码/提示词刷新、用户 config 不覆盖、用户 data 不丢、幂等不回滚）
  - F8 打回清下游 + 审批 CLI 全链
  - S9 / S10 回归
- **反向验证**：S9、S10 两处修复均经「还原缺陷 → 用例必须失败 → 恢复 → 必须通过」验证：
  - 还原 S9 → `KeyError: '8'`、`STAGE_KEYS 覆盖 7 >= 8` 断言失败
  - 还原 S10 → 退出码 `0`、`runs=[(1,'done')]`、`budget.paused=False`（**正是静默宣告成功**）
- 回归确认：全部既有单元套件 + 8 个 HTTP 套件（合计 263 断言）全绿；
  GUI 构建 `322 modules transformed` 成功。

### ⚠️ 尚未处理

- `snapshot.py` 仍无 `--restore` 入口（恢复路径未演练）。
- `rotate_days` 仍是死配置（无 `RotatingFileHandler`）。
- `nf_api.py` 2,785 行 God Object 未拆分（P2，须保持 65 断言全绿）。
- 全仓仍有 46 条 pyflakes 历史告警（未使用导入 / f-string 无占位符），
  当前策略是「只计数不阻塞」，避免一次性冻结门禁。
- `tests/unit/test_thinking_compat.py` 的 2 条**真实探针**断言当前失败：
  探针假定「不关思考时 content 必为空」，但 TokenHub 上的模型现已在默认
  模式下把正文正常放入 `content`（该行为变化本身是好事）。
  离线部分 28 断言全绿；这两条属于**探针假设过期**，与代码缺陷无关
  （`llm_client.py` 本轮未被改动）。建议改为「记录事实」而非硬断言。

> ↑ 以上 4 项已在**第三轮**全部处理完毕，见下。

---

## 第三轮：清掉「尚未处理」全部 4 项 + 3 个新缺陷（2026-09-19 深夜）

第二轮列了 4 项待办，这一轮**全部做完**，并在过程中揪出 3 个此前无人知道
的真缺陷（其中一个属于「静默失败」中最阴的一类）。

### ✅ 待办 1：`test_thinking_compat.py` 探针硬断言过期 → 改为事实记录

原断言假定「不关思考时 `content` 必为空」，但 TokenHub 上的模型已在默认模式
把正文正常放进 `content`。这不是代码坏了，是**探针对现实的假设过期了**。
改为「记录实测事实」：探针照跑、结果照打，但只在**确认是缺陷**时断言失败。
现 **34/34 全绿**。

### ✅ 待办 2：`snapshot.py --restore` 补齐（恢复路径首次可演练）

此前快照**只写不读** —— 510 个文件的备份躺在 `history/`，却没有任何恢复入口，
「可回退」是纸面承诺。新增 `--restore`，安全设计按危险程度递减：

| 层 | 机制 |
|---|---|
| 1 | **默认 dry-run** —— 不带 `--yes` 只列「将覆盖什么、将保留什么」 |
| 2 | **恢复前自动打 `pre_restore` 快照** —— 恢复错了还能折返 |
| 3 | **删除需显式开启** —— 快照外的文件默认**保留**，`--delete-extra` 才删 |
| 4 | **范围限定** —— 只恢复 `SNAPSHOT_ITEMS`，绝不整棵 `data/` 还原 |
| 5 | **路径护栏** —— `history_dir` 必须含 `history` 段；ID 精确/唯一前缀匹配，禁穿越 |

### ✅ 待办 3：`rotate_days` 死配置 → 接通日志轮转

`config/system.yaml` 里 `logging.rotate_days: 30` 此前**没有任何代码读它**。
后果：Electron 以 append 模式无界追加 `%LOCALAPPDATA%/Temp/nf_api_child.log`，
长跑把日志撑到几百 MB 且永不清理。

新增 `scripts/utils/run_log.py`（`get_log_path` / `rotate_if_needed` /
`append_line` / `tail` / `log_status`），在 `nf_api.py` 启动前调用一次。
设计取舍：**按天轮转不按大小**（配置项语义就是时间；按大小会让「读哪个文件」
不可预测）；**不改 Electron 的 spawn 方式**（那要重启控制台才生效，而 Python 侧
每次起服务都会走到）；**轮转失败绝不阻断启动**（日志坏了不连累主流程）。

### ✅ 待办 4：`nf_api.py` God Object 拆分（P2）

**2,872 → 2,374 行（-17%）**，91 个端点分支（43 GET + 48 POST）**全部**改为
薄转发，实现搬进 `scripts/nf_api_domains/`：

| 模块 | 端点 | 内容 |
|---|---|---|
| `contract.py` | — | 接缝协议：`(status, payload)` 返回约定 + `STREAM_RESPONSES` 哨兵 |
| `misc.py` | 8 | 成本/环境/进度/质量/日志/关于（试点域） |
| `models.py` | 6 | 模型列表与切换（两级校验，S8 的发生地） |
| `materials.py` | 6 | 素材/碎片/设定**读**侧 |
| `outline.py` | 6 | 结构化大纲/版本/diff/草稿/分章 |
| `project.py` | 6 | 健康检查/状态/多书/结构树/配置 |
| `runtime.py` | 9 | job/审稿报告/提示词/校对/预估/节奏/章节历史 |
| `post_misc.py` | 1 | Markdown 导出 |

**拆分纪律（三条，全部由测试守护）**：
1. **谁发响应永远只有一个答案** —— 域模块只 `return (status, payload)`，
   绝不碰 `h._send`；`nf_api.py` 用 `_dom()` 统一展开。
2. **反向依赖必须走属性访问** —— 域模块写 `import nf_api as api` + `api.ROOT`，
   **禁止** `from nf_api import ROOT`（后者把 `ROOT` 拷成死值，
   使 `--root` 与测试的临时项目根失效）。
3. **分发器留在 `nf_api.py`** —— 契约测试以 `Handler.do_GET/do_POST` 为路由表锚点。

配套把 `tests/e2e/test_gui_api_contract.py` 从「正则切源码块」改为 **AST 解析**
（27 → 35 断言），这是拆分的**前置解锁条件**：原实现把 `do_GET`/`do_POST`
钉死在 `nf_api.py` 里，正则一改就误报。

### 🔴 新缺陷 S11：`__main__` 双份 `nf_api` 实例 → `/jobs/{id}` 永远 404

**发现方式**：拆完 `runtime.py` 后跑 HTTP 回归，`test_auto_rewrite_api_http`
从 17/0 掉到 14/3，失败项全是 `{'state': 'timeout'}`。

| 项 | 内容 |
|---|---|
| 现象 | `python scripts/nf_api.py` 启动时，`GET /jobs/{id}` 恒返回 404；前端轮询后台任务全部超时 |
| 根因 | 以脚本方式启动时本文件模块名是 `__main__`；域模块里 `import nf_api as api` 会**再加载一份**，进程内同时存在两个 `nf_api`，`JOBS` / `CURRENT` / `ROOT` 是彼此独立的对象 |
| 为什么静默 | 从**磁盘**读文件的端点（`/state`、`/costs/…`）全正常 —— 它们不依赖进程内状态；只有 `/jobs/{id}` 这类查内存字典的会坏 |
| 为什么单测抓不到 | 直接 `import nf_api` 的单测只有**一份**模块，全绿。这个缺陷**只在真机 HTTP 下暴露** |
| 性质 | 「静默失败」—— 前端显示「正在运行」却永远等不到结果，与 S2 同类 |

**修法**：在 `nf_api.py` 导入区加标准别名守卫：

```python
if __name__ == "__main__":
    sys.modules.setdefault("nf_api", sys.modules["__main__"])
```

并新增**四层判据**（`tests/unit/test_api_domains.py` 第 6 节）：静态检查守卫存在
+ 子进程里按源码守卫实跑 + 断言 `m is api` + 断言 `m.JOBS is api.JOBS`。
反向验证：移除守卫 → 该节 4 条 FAIL **且** `test_auto_rewrite_api_http` 3 条 FAIL。

### 🔴 新缺陷 S12：`snapshot --restore` 的 extras 检测漏掉子目录

写 `--restore` 回归用例时，用例断言「子目录里新增的文件应被识别为 extras」失败。

| 项 | 内容 |
|---|---|
| 现象 | 快照后新增的 `data/chapters/raw/99.md` 不出现在恢复计划里，`--delete-extra` 对它完全失效 |
| 根因 | `_collect_plan` 只扫「目录型 item」的**下一层**：`covered_parents` 由 `Path(i).parent` 得来，于是 `data/outline`、`data/chapters` 进了列表，但它们的子目录（`data/outline/chapters`、`data/chapters/raw`）没进 |
| 不对称点 | 恢复动作是 `shutil.copytree`（**整棵**替换），而 extras 检测只扫一层 —— 两者口径不一致 |
| 后果 | 用户看不到「恢复会失去哪些新章节」，`--delete-extra` 形同虚设 |

**修法**：extras 改为按**目录型 item 的整棵子树递归**比对（`_scan` 递归 +
遇到「工作区多整个目录」时逐个列出文件，而不是给一个笼统目录名）。
反向验证：退回单层扫描 → 用例 4 条 FAIL。

### 🔴 新缺陷 S13：`run_log.load_logging_cfg` 遇到结构错配置会抛

新写的 `test_run_log.py` 里「坏配置不得抛异常」用例抓到：
`logging:` 被写成标量（如 `logging: info`）时，`section.get` 抛
`AttributeError`。调用方是**启动路径**，配置写歪不该让服务起不来。

**修法**：非 dict 时回退 —— 若是字符串则**当作 level 值**（比整个丢掉更友好），
否则用默认值。

### ✅ 测试

本轮新增 **3 个回归套件（91 断言）**，全部经反向验证：

| 套件 | 断言 | 守护对象 |
|---|---|---|
| `tests/unit/test_run_log.py` | 37 | 日志轮转（超期归档/清理白名单/级别过滤/解包顺序/dry-run/容错） |
| `tests/unit/test_snapshot_restore.py` | 30 | `--restore` 真实演练（dry-run 零改动/pre_restore 折返点/extras 递归/范围限定/路径护栏） |
| `tests/unit/test_api_domains.py` | 24（原 18） | 拆分纪律 + `__main__` 别名守卫 |

另**修复 1 个被重构打破的旧用例**：`test_config_and_models.py` 的第 D 节
按分支边界切 `nf_api.py` 源码文本再 grep，实现搬家后误报红。
改为「先定位实现所在文件（顺转发目标找到域模块），再在文件内断言」——
**契约不变，位置可变**。修完 29/29，并反向验证仍能捕获真实回归。

**全量回归（本轮结束状态）**：

```
契约测试                 35/35
域模块护栏               24/24
HTTP 套件 × 8           263/263
failure_paths            69/69
stream_stage             18/18
stop_and_mingjian        38/38
config_and_models        29/29
run_log / snapshot       37 + 30
pyflakes 门禁            BLOCK=0 ✅
```

### 📌 已知技术债（本轮有意保留，未处理）

- **`/review/report`、`/proofread/report`、`/book/pacing` 用相对路径**
  （`Path("data/...")`，依赖进程 CWD），而同族其它端点用 `api.ROOT / ...`。
  拆分时**刻意保留原行为** —— 改成 `ROOT` 会让 `--root` 场景的语义变化，
  那属于行为变更，不该混在重构里做。已在 `nf_api_domains/runtime.py`
  的模块 docstring 里记录，建议后续单独处理。
- 全仓 pyflakes 历史告警 52 条（较第二轮 +6，来自新域模块的反向属性访问模式），
  策略仍是「只计数不阻塞」。

> ↑ 第一条已在**第四轮**修掉（有了「确实会读错项目」的实证后，它不再是取舍问题）。

---

## 第四轮：实证驱动的两项清理（2026-09-20）

第三轮结束时留了两条技术债，其中一条当时判为「行为变更，须单独处理」。
本轮先做**实证**再决定 —— 结论是：它不是取舍问题，是真缺陷。

### 🔴 S14：相对路径 IO 在 `--root` 场景静默读错项目

**发现方式**：不再靠推理，直接构造场景实测：

```
A = 书本A（有 review_report.json，marker=FROM_A）
B = 书本B（--root 指向它）
cwd = A，--root = B
```

| | 修前 | 修后 |
|---|---|---|
| `GET /review/report` | `200 {"marker": "FROM_A"}` ❌ | `200 {"marker": "FROM_B"}` ✅ |
| `GET /proofread/report` | 读到 A 的文件 | `404 暂无校对报告`（正确跟随 B）✅ |
| `GET /book/pacing` | 读到 A 的拆书产物 | `404 尚无拆书结果` ✅ |

**为什么至今没爆**：Electron 主进程是 `spawn(cmd, args, {cwd: ws})`
**且**同时传 `--root ws` —— CWD 与 ROOT 恰好相等。
但 `--root` 这个参数的存在本身就说明**设计允许两者不同**，
所以这是**潜伏缺陷**：一旦真的用它指向别的书档，读到的是 CWD 的书，
且不报错、不警告。

**修法**：17 处 `Path("data/...")` / `api.Path("logs/...")` 全部改为
`ROOT / ...` / `api.ROOT / ...`。影响文件：

| 文件 | 处数 | 端点族 |
|---|---|---|
| `scripts/nf_api.py` | 9 | `/costs` `/costs/summary` `/refine/*` `/review/run` `/batch_refine/run` |
| `nf_api_domains/misc.py` | 4 | `/costs/summary` `/batch_refine/progress` `/chapters/quality` `/chapters/verify` |
| `nf_api_domains/runtime.py` | 4 | `/review/report` `/review/decisions` `/proofread/report` `/book/pacing` |

**回归风险为零**：Electron 场景 CWD==ROOT，测试场景 CWD==临时根，
两种情况下 `ROOT / ...` 与 `Path("...")` 解析到**同一个路径**。
8 个 HTTP 套件（263 断言）全绿佐证。

**护栏**：`tests/unit/test_api_domains.py` 新增第 7 节，用 AST 找
`Path("<字面量>")` 里以 `data/` `logs/` `output/` `config/` `prompts/`
`materials/` `templates/` 开头的调用。反向验证：把 `runtime.py` 退回相对路径
→ 断言 FAIL 且精确定位 `runtime.py:62`。

### 🔴 S15：`run_log.log_status` 的 `age_days` 会出现 `-1 天`

**发现方式**：核对「文档声明断言数 vs 实测」的脚本里，
`test_run_log.py` 某次跑出 36/1 而不是 37/0 —— 复跑 3 次又全绿，
说明是**偶发**，顺藤摸到了根因。

| 项 | 内容 |
|---|---|
| 现象 | 刚写的日志，`log_status()["age_days"]` 偶尔返回 `-1`；`rotate_if_needed` 的 reason 里也出现 `age=-1 天` |
| 根因 | `timedelta.days` 对**负值向下取整**。而 mtime 可能比 `now()` 晚哪怕 1 微秒（文件系统时间戳精度高于两次 `now()` 之间的间隔），于是差值成了负的微秒级 timedelta |
| 后果 | 展示层出现「-1 天」这个不可能读数，会让人以为时间算错了 |

**修法**：抽出 `_age_days(mtime)`，内部 `max(0, ...)` 收敛；
`rotate_if_needed` 的 reason 与 `log_status` 都改走它。
**同时**把测试里那条 `in (0, 1)` 的断言收紧为 `== 0`
（当初写 `in (0,1)` 是为了「防跨午夜」，结果**掩盖了这个真缺陷**），
并新增一条「把 mtime 推到未来，age_days 仍须为 0」的回归用例。

反向验证：去掉 `max(0, ...)` → `age_days` 变 `-1` → 断言 FAIL。

### 📝 文档订正

- `AGENTS.md` 里 `test_auto_rewrite.py` 声明 39 断言，实测 **43**
  （该数字在第二轮回填后没再更新）。已订正。
- 同表补齐本轮变动的两项：`test_run_log.py` 37 → 38、
  `test_api_domains.py` 24 → 25。
- `AGENTS.md` 架构章节新增「路径 IO 必须经 `ROOT`」纪律与 S14 的反例。

### ✅ 全量回归（第四轮结束状态）

```
契约测试                 35/35
域模块护栏               25/25      （+1：无相对路径 IO）
HTTP 套件 × 8           263/263
failure_paths            69/69
stream_stage             18/18
stop_and_mingjian        38/38
config_and_models        29/29
run_log                  38/38      （+1：负值收敛回归）
snapshot_restore         30/30
pyflakes 门禁            BLOCK=0 ✅
```

### 📌 仍未处理

- **pyflakes 历史告警 52 条**（未使用导入 / f-string 无占位符）。
  策略仍是「只计数不阻塞」—— 一次性清零会冻结门禁，收益低于成本。
- **`tests/e2e/e2e_ux_verify.py`（声明 64 断言）未纳入常规回归**：
  它依赖 Playwright + headless Chromium + 已构建的 renderer，
  跑一次成本高。本轮改动仅涉及 API 层后端逻辑，未触碰前端，
  故未运行。**建议后续把它固定进「发版前必跑」清单**。

---

## 第五轮：产出契约加固 + thinking 兼容 + 仓库实证修复（2026-09-25 ~ 09-28）

### 🔴 产出契约加固（2026-09-25）

- **`_post_chat` / `_post_chat_stream` 返回 4 元组** `(text, usage, model, finish_reason)`。
  `finish_reason == "length"` 表示输出被 max_tokens 截断、正文不完整 —— 此时**不落盘**、
  返回 `exit_code: 2`、残缺文本隔离存 `data/state/truncated/`。
- **背景**：思考模型（glm-5.x / kimi）默认进 thinking 模式时，正文全进 `reasoning_content`、
  `content` 为空（且思考 token 吃掉 `max_tokens` 预算）→ 流水线产出空白。
- **防线**：① `disable_thinking_models` 名单内模型自动注入「关闭思考」片段；
  ② 未列名单的思考模型由客户端自动探测并注入后重试；
  ③ 兜底**只取协议块**（`===FILE:` / `===APPEND:` / `===DELETE:`）：reasoning_content 里找不到
  协议块就**直接丢弃**、返回空让上层判失败。

### 🔴 thinking 兼容加固（2026-09-27）

- `minimax-m2.7` 已知怪癖：①JSON 产物偶发非法转义符 ②关闭思考 payload 非 100% 生效
  （~10% 概率仍进 thinking）③标题格式偶用单#而非双##。
- 三重防线已加固（标题容错+换候选重试+raw fallback）。

### 🔴 仓库实证审查修复（2026-09-28）

依据外部在线 agent 对 GitHub 仓库的审计报告（`绒花墨坊-仓库实证审查报告-20260928.md`），
修复 P0 七件小修：

- **README**：`MYU46548`→`MUYU46548`（2 处）、`开发日志.md`→`CHANGELOG.md`、
  `Temp/`→`tests/`、补 requirements-dev 说明
- **三个 .bat 启动脚本**：硬编码本机绝对路径 → `%~dp0`
- **`nf_api_selftest.py` + 8 个 HTTP 测试**：硬编码 `.venv/Scripts/python.exe` → `sys.executable`
- **`config/project.yaml`**：删除重复 `user_outline` 键（保留真实大纲 128 字符）
- **`project_config.py`**：新增 `_UniqueKeyLoader` 重复键守卫（PyYAML 默认静默取后值）
- **AGENTS.md**：MCP 旧口径更正（"没有 MCP 层" → "三条通道"含 MCP）


