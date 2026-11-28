# 架构：API 层的域模块拆分（P2）

> 原 `AGENTS.md` 的一节，2026-11-28 拆分至此（AGENTS.md 已达 61,239 字节，逼近 65,536 字节的
> 指令加载预算 —— 超限会被系统**静默截断**，且截掉的正是靠后的《硬性约束》《能力边界》）。
> **纪律效力不变**：动 `scripts/nf_api*` 前按需读本文。


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
| ~~`models.py`~~ | **2026-11-25 已删除**（F8：模型/服务商端点下线，实现无处安放） | 0 |
| `outline.py` | 大纲结构/历史/差分/drafts/趋势/建议/沙盒队列 | 10 |
| `materials.py` | 素材/碎片读写 | 5 |
| `misc.py` | 章节质量/验证/日志/关于/成本/批量进度 | 6 |
| `post_misc.py` | Markdown 导出 | 1 |
| `sandbox.py` | 沙盒审核/文件预览 | 2 |
| `refine.py` | 大纲精修/节点精修/撤销/章节精修 | 4 |
| `setting_state.py` | 设定集冲突/canon/状态回写 | 3 |
| `factory.py` | 出厂清单盘点 / 出厂样例一键归档（0.6.7） | 2 |
| `book_extract.py` | 成书提炼：预演 / 建目录 / 后台跑 / 并库（0.7.13） | 6 |

#### Agent 模式（外部 Agent 控制，P1 新增）

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
  - ~~`/providers/save`、`/providers/remove`~~ —— **2026-11-25 端点已删除**（F8：墨坊不再配置服务商与 Key）。POST 现在回 410“该能力已移除”，只在名单里留这条说明；`GET /providers` 也已下线。
  - `/factory/clean`（出厂样例清理 —— 移动用户素材文件；只读 `/factory/list` 不拦）
  - `/stage/skip`（跳过阶段会把状态标成 done+已审批+skipped —— 与审批同族，2026-10-16 补）
  - `/project/archive/delete`（删除归档 = 抹掉成书数据；端点另要求 `X-Mofang-Source: gui`）
- Agent 生成的大纲/产物自动进入待审批状态（exit=3），需在桌面端确认
- 双保险机制：Agent 被告知需在桌面端审批 + 桌面端自动检测审批门并弹窗提示
- 审计日志：所有 Agent 调用记录到 `data/state/agent_audit.jsonl`

**前端配合**：
- “设置”页新增“Agent 模式”toggle（二次确认）
- 顶部连接状态旁显示 `⚡ Agent` 徽标（橙色脉冲动画）
- 全局刷新按钮（↻）一键刷新所有页签数据
- 审批门产物摘要显示 `agent: true/false` 标记

#### Agent 可调用端点清单

**✅ 可调用**（查询类）：
- `GET /state`、`/health`、`/about`
- ~~`GET /models`、`/models/available`、`/models/cache`、`/models/fetched`、`GET /providers`~~ —— **2026-11-25 全部下线**（F8）。引擎名改读 `GET /state` 的 `engine` 字段；模型名与渠道由 DSH profile 决定，墨坊不再提供查询面。
- `GET /whoami`（服务端认为“你是谁”：可信 GUI / 三种拒因 + 来源声明 + 有无令牌 + 引擎 + agent_mode；**不含令牌值**。作者点按钮失败时靠它分清“我没权限”还是“功能坏了”）
- `GET /config/project`、`/config/style_notes`、`/config/agent_mode`、`/config/target_words`
  （单章字数区间：当前值 + 出厂预设 + 合法区间）
- `GET /config/vault_scan`（知识库扫描面：**顶层目录名取自用户库本身** + 各自篇数 +
  是否被排除 + 用户排除项/默认项分列。目录名一律不写进代码 —— 判据见 `docs/agents/kb-scan.md`）
- `GET /outline/structure`、`/outline/trend`、`/outline/advise`、`/outline/history`、`/outline/diff`
- `GET /outline/segments`（丙4 分段审阅：分段清单 + 每段正文 + 放行判据回显；**读侧不设闸**）
- `GET /materials/reconcile`（索引与实物对账：登记表声称的篇数/日期 vs 盘上实物；
  `verdict` ∈ consistent | mismatch | no_meta，零 token 只读）
- `GET /materials/scan`（阶段 1 读入预览：会读 N 篇 / 跳过 M 篇 + 逐个文件名与原因；
  判据与 stage1 同一份 `utils.meta_detect`，零 token 只读）
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

#### ⚠️ 路径 IO 必须经 `ROOT`，不得用相对路径

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

#### ⚠️ `__main__` 别名守卫（勿删）

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
