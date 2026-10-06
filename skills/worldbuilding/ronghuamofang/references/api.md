# nf_api 端点速查

来源：`scripts/nf_api.py`（头部 docstring 是活文档，代码为准）。
服务：`python scripts/nf_api.py`，默认 `127.0.0.1:8765`。
只读转发（推荐给 Agent，省掉手写 curl）：`python scripts/nfctl.py api <路径>`。

**安全边界**：默认只绑 127.0.0.1；写操作只允许 POST 且 `Content-Type: application/json`；
除 `/stage`、`/refine`、`/snapshot` 外均**即时执行**（不是排队）。

---

## Agent 模式守卫（先看这条）

`config/system.yaml` 的 `gates.agent_mode = true` 时，**非 GUI 来源**
（请求头没有 `X-Mofang-Source: gui`）调用以下 POST 端点返回 **403**：

```
/approve  /reject  /project/create  /project/archive  /project/restore  /project/init  /config/agent_mode
```

其余 POST 允许，但会写审计日志。**GET 完全不受限。**
含义：外部 Agent 可以读一切、可以跑流水线与精修，**但不能代替用户审批/归档/切模式**。
`nfctl.py` 转发时带 `X-Mofang-Source: agent`，与 GUI 来源区分开。

---

## GET

### 健康 / 状态 / 环境
| 路径 | 说明 |
|---|---|
| `/health` | 存活 + 当前 job（`{ok, current_job, current_kind, allow_fake}`） |
| `/state` | progress + gates + 成本汇总 + 最近 job（**Agent 最常用的一站式读**） |
| `/about` | 版本 / 运行环境 / 路径 |
| `/models` | system.yaml 的 engine/providers/model 回显 |
| `/models/available` / `/models/fetched` / `/models/cache` | 可选模型 / 已拉取 / 缓存（含手动添加） |
| `/config/project` | project.yaml 回显 |
| `/config/style_notes` | 用户风格笔记 |
| `/config/agent_mode` | Agent 模式开关当前值 |
| `/env/open` | 打开 .env 所在位置（本机动作） |
| `/logs/tail` | `?lines=200` nf_api + orchestrator 输出尾部（排障用） |

### 素材 / 碎片 / 设定
| 路径 | 说明 |
|---|---|
| `/materials/list` | 素材卡列表 |
| `/materials/read/{name}` | 读单张素材卡 |
| `/scraps/list` | 原始碎片：按簇分组 + 时间轴 + 前瞻备忘（确定性，零 LLM） |
| `/scraps/read` | `?name=` 读单个碎片 |
| `/setting/current` | 当前设定集 |
| `/setting/appearances` | 角色出场统计 |

### 大纲
| 路径 | 说明 |
|---|---|
| `/outline/structure` | 结构化大纲视图（acts/nodes/plan/评分） |
| `/outline/history` | 版本列表（含评分摘要） |
| `/outline/diff` | `?v1=&v2=` 节点级 diff |
| `/outline/drafts` | 阶段2 多方案 draft 列表 |
| `/outline/trend` | 迭代趋势：每轮指标 + 与上一版对比 + **收敛判定**（零 token） |
| `/outline/advise` | 开工方向建议：按严重度排序的候选方案 + 可执行 next_step（零 token） |
| `/outline/chapters/list` / `/outline/chapters/get` | 逐章大纲 |

### 章节 / 审稿 / 校对
| 路径 | 说明 |
|---|---|
| `/chapters/history` | `?n=3` 第 3 章的历史版本 |
| `/chapters/quality` | 章节质量分 |
| `/chapters/verify` | 退化判据校验（`utils/verify_chapter`） |
| `/review/report` / `/review/decisions` | 审稿报告 / 用户决策 |
| `/batch_refine/progress` | 批量精修进度 |
| `/proofread/report` | 校对报告（stage 5.5，schema 与 review_report 对齐） |

### 成本 / 预估 / 拆书
| 路径 | 说明 |
|---|---|
| `/costs` | 成本流水（最近 100 条） |
| `/costs/summary` | 按阶段/模型聚合 |
| `/costs/streaming` | 当前 run 实时消耗 + 预算进度（GUI 轮询用） |
| `/costs/rates` | 定价表（GUI 定价编辑器） |
| `/estimate` | `?stage=4` / `?stages=1,2` / `?no_history=1` token+费用预估（零 LLM） |
| `/book/pacing` | 拆书/章节节奏结果 |

### 沙盒 / 知识库
| 路径 | 说明 |
|---|---|
| `/sandbox/queue` | 沙盒审核队列：待审 + 状态统计 + 孤儿文件（零 token） |
| `/sandbox/file` | `?path=<相对路径>` 只读预览沙盒产物正文（审核前必须看得见内容） |
| `/kb/search` | `?q=&top=&chars=` 知识库检索 |
| `/kb/build` | 构建知识库索引（同步，可能慢） |

### 任务 / 流式
| 路径 | 说明 |
|---|---|
| `/jobs/{id}` | job 状态/结果 |
| `/stream/{job_id}` | **SSE** 实时流式输出（token 级） |
| `/project/status` / `/project/list` | 书档状态 / 列表 |
| `/prompts/list` / `/prompts/get` | 提示词模板列表 / 正文（`?name=` 或 `/prompts/get/<name>`） |

---

## POST

### 跑阶段与流控
| 路径 | 请求体 | 说明 |
|---|---|---|
| `/stage/{n}/run` | `{from_stage?, only_stage?, stream?}` | 返回 job_id（202）；**409 = 已有任务在跑** |
| `/stop` | `{job_id}` | 中断（流式置 stop；非流式下个阶段边界退出，退出码 4） |
| `/stream/pause` / `/stream/resume` | `{job_id}` | 流式暂停/恢复 |
| `/stage/skip` | `{stage, confirm:true, reason?}` | 人工跳过（标记 done + 已审批，**不改产物**） |
| `/stage/2/run-multi` | `{count}` | 阶段2 多方案生成（2-5 份 draft） |
| `/outline/compose` | `{selections, act_source}` | 多方案拼合为 global.md |
| `/outline/drafts/cleanup` | — | 清理临时 draft |

### 审批 / 打回（★ Agent 模式下 403，必须由用户执行）
| 路径 | 请求体 | 说明 |
|---|---|---|
| ★ `/approve` | `{stage, revoke?}` | 审批 / 撤销审批 |
| ★ `/reject` | `{stage, reason, dry_run?}` | 打回（**默认真执行**，清下游 + 重置状态） |
| ★ `/project/create` | `{name, genre?, chapters?, style_notes?, archive_current?}` | 新建书档（归档当前 → 初始化 → 写 project.yaml） |
| ★ `/project/init` | 书名/类型/章数 | 首次向导 |
| ★ `/project/archive` / ★ `/project/restore` | `{name}` 等 | 归档 / 恢复书档 |
| ★ `/config/agent_mode` | `{value}` | 切换 Agent 模式（Agent 不得自行开关） |

### 修订 / 精修
| 路径 | 请求体 |
|---|---|
| `/refine/outline` | `{feedback, dry_run?}` |
| `/refine/outline/node` | `{node_id, feedback, dry_run?}` |
| `/refine/outline/undo` | 撤销上一次节点精修 |
| `/refine/chapter` | `{chapter, feedback, dry_run?}`（改稿前自动备份） |
| `/chapters/restore` | `{n, version}`（回退前再存一版当前稿） |
| `/outline/save` | `{content}`（格式校验 + 备份） |
| `/outline/restore` | `{version}` |
| `/outline/chapters/save` | 逐章大纲保存 |
| `/auto_rewrite/run` | `{threshold?, max_rounds?, chapters?, dry_run?}`（**dry_run 默认 true**） |
| `/review/run` | 章节审查 |
| `/review/comment` / `/review/decisions` | 审稿批注 / 决策 |
| `/batch_refine/run` | 按审查报告批量精修 |
| `/proofread/run` | `{scope?, llm?, dry_run?, report?}` |
| `/snapshot` | `{label?}` 手动快照 |
| `/appearances/refresh` | 重算出场统计 |

### 内容 / 配置写入
| 路径 | 请求体 |
|---|---|
| `/setting/save` | 设定集写入 |
| `/materials/add` / `/materials/new` / `/materials/save` | 素材卡增改 |
| `/materials/delete` | 删除（破坏性） |
| `/materials/remerge` | 触发重归并 |
| `/scraps/save` | 保存碎片（写前备份） |
| `/scraps/delete` | `{name, confirm:true}`（**先快照再删**） |
| `/scraps/promote` | `{card_name, card_type, points[], open_questions[], sources[]}` → 落盘 `materials/raw/<名>_<类型>.md` |
| `/config/project` | project.yaml 定向写入 |
| `/config/style_notes` | 风格笔记写入 |
| `/config/provider` | provider 配置 |
| `/prompts/save` | `{name, content}`（白名单 `prompts/stage[1-7]_*.md`，备份到 `prompts/history/`） |
| `/models/add` / `/models/switch` | 模型缓存 / 切换（**换模型需用户确认**） |
| `/style/analyze` | `{source, path?, n?, scope?, compare?}` 文风特征 + 偏差 |
| `/book/split` | `{path, emit?}` 拆书（输入只读） |
| `/export/markdown` | `{per_vol?, book_name?}` 分卷导出 |
| `/sandbox/review` | `{path, action: approve\|reject\|reset, note?}`（只改状态库，不写不删沙盒文件，绝不碰 vault） |
| `/costs/rates` | 定价表保存（GUI 定价编辑器） |

---

## 与 GUI 的关系

Electron 控制台（`console/`）消费的就是这套端点，页签 ↔ 端点大致对应：
流水线 `/state` + `/stage/*` · 大纲 `/outline/*` · 审稿 `/review/*` · 审核 `/sandbox/*` ·
成本 `/costs/*` · 校对 `/proofread/*` · 文风 `/style/analyze` · 设置 `/config/*` + `/prompts/*`。

**注意**：GUI 启动时会探测 8765，被占用则**杀掉占用者**再起自己的后端
（`console/main/index.js` 的 `startApi`）。所以手动起的服务可能被 GUI 顶掉 ——
两边的"当前工作区"也可能不同（源码版 = 项目根，安装版 = `%APPDATA%\绒花墨坊\workspace`）。
