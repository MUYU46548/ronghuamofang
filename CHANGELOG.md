# 更新日志（CHANGELOG）

本项目此前无版本记录（2026-09-19 审查发现「已有 v0.2.0、NSIS 发布等版本行为却无
CHANGELOG」）。本文件从工程审查修复起正式启用。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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


