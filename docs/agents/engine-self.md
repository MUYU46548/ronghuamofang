# engine: self —— 阶段只产任务书，由调用方执行

> 状态：**已实施**（2026-11-04）。设计稿留在下面当"为什么"的出处；
> 实施口径见《实施记录》，两者不一致时以实施记录为准。
> 为什么单独立文：这是本项目唯一一处“改错会让整条流水线语义变形”的改动，
> 不能在会话末尾顺手开工（台账里已有“半途开工”的教训）。

## 实施记录（2026-11-04）

落地成**三件套**，比原设计的"两步"多一样东西，原因都写在下面：

| 入口 | 位置 | 作用 |
|---|---|---|
| `emit_tasks(cfg, proj, progress=None, task_dir=None, **_extra)` | 阶段 1~6 各自模块 | **零 token** 产出任务书，返回 `{stage, tasks[{path,target,check}], hint, done, blocked}` |
| `self_done(cfg, proj, progress=None, **_extra)` | 阶段 1~6 各自模块 | 判“产物真的齐了”，返回 `(bool, 原因)`。**没有它的阶段**回落 `missing_artifacts` |
| `settle_self(...)` | 阶段 1 / 4 / 6 | 产物到位后的**确定性收尾**（补摘要补账 / 合并润色报告 / 碎片多批合并 / vault_links） |

公共部分在 `scripts/utils/self_tasks.py`（协议头、`strip_protocol`、统一返回形状）。
编排侧：`orchestrator._self_stage`（约 60 行），流程图就是设计里那三行。

### 为什么不只用 `artifact_gate` 判完成（原设计漏的一点）

`STAGE_ARTIFACTS[4]` 是 `data/chapters/raw` **目录非空** —— 对 self 模式这是致命的：
写第 1 章就算“阶段 4 完成”，剩下 39 章直接被跳过。所以每个阶段都给了 `self_done`，
判据与真跑同一份实现（阶段 4 = 每章 `is_chapter_complete`；阶段 5/6 = `is_usable_output`；
阶段 2 = 结构 + 锚点；阶段 3 = 窗口内 `outline_ok`；阶段 1 = `validate_setting`）。

### 与原设计的三处刻意差异

1. **阶段 4 一章一交回**：每章任务书要吃“上一章末尾衔接段”与“滚动摘要”，
   一次发 N 份等于后 N-1 份的上下文全是空的。所以 `emit_tasks(4)` 只出**下一个**
   待写章；写完重跑再出下一章。`settle_self` 会把已落盘章的 `<!-- summary -->`
   补进 `rolling.md`（只补**合格**的章 —— 半成品补摘要等于谎报完成）。
2. **阶段 5 的账本要求写进协议头**：`build_check_task` 本来就要求 `data/review/batchN.json`，
   self 模式下没人替执行方写，所以协议头的"合格判据"与附注明写这件事。
   ⚠️ **已知边界**：`self_done(5)` 只判 checked 稿可用，不校验账本完整性 ——
   账本缺了不会被 self 分支拦下（真跑里 `_verify_ledger` 会拦）。这是刻意的取舍：
   账本是给判定引擎用的旁证，不是"检查做没做"的唯一凭据。
3. **`make_client` 在 self 下直接 `RuntimeError`**：`resolve_engine` 把 `self` 列为合法引擎，
   `make_client` 见到 `self` 立刻抛错。理由：静默回落 direct/hermes = "配了 self，
   后台却在真花钱跑模型"，与整个卖点相反。任何阶段误走 `run_stage` 都会当场炸出来。

### self 模式下**不执行**的既有钩子（如实声明）

`_post_stage` 里的这些都要模型或要人，self 分支一概不走：`auto_rewrite`、
`review_after_stage4`、`semantic_review`、`setting_refine_auto`、`proofread`（LLM 路）。
阶段前/后快照也不做（self 不改既有产物，任务书是新增文件）。
**审批门照旧**：产物齐 → 标 done + `approved=False` → 下一阶段在审批门停下（退出码 3）。

### 对外可见的接口

- `nfctl status`：`awaiting_external` 显示成 `[外]` + “任务书 N 份（等外部执行）”
- `GET /state`：`stages[n].status = "awaiting_external"`，带 `tasks` / `task_hint`
- 任务跑完（exit=3）时 `nf_api._rc_to_result` 说“任务书已生成（阶段 N，M 份）”，
  不再冒充“等待审批”
- 桌面端：设置页“运行引擎”第三张卡（只出任务书）；阶段徽标“等外部执行”
- `estimate_tokens.py`：self 下预估恒 0 并注明“不调用任何模型”

### 判据

`tests/unit/test_engine_self.py`（15 条，`nfctl test` 会跑）：T1~T4 管任务书本身，
S1~S5 管编排与回归（产物缺 → rc 3；重跑幂等；产物齐 → 直接 done；
direct 不产 self 任务书；`make_client` 必须炸）。


## 要治的病

| 现象 | 根因 |
|---|---|
| 每个阶段都要起一次 hermes 子会话，慢 | `make_client` 在 `engine: hermes` 下返回 `HermesClient`，每次 `run_task` 都是一次冷启动（读配置 / skills） |
| 阶段 2 尤其慢 | 同上 + 单阶段任务书体积大 |
| 跑完不知道为什么慢 / 花了多少 | 子会话的成本恒 0（订阅流量），只有 token 数 |
| Agent 想接管时只能“看着它自己跑” | 没有“你给我任务书，我来执行”的接口 |

`engine: self` 的语义：**程序不调用任何模型**，只把“这一步该写什么、写到哪、
什么样算合格”写成任务书；调用方执行完后，程序下次运行时按盘上产物续跑。

## 现状事实（读代码得到，实施时按这些走）

1. 所有客户端构造都经 `utils/llm_client.make_client(cfg, role)` —— 唯一注入点。
2. 阶段调用统一为
   `STAGES[n].run_stage(cfg, proj, progress, db, cost, client=client, run_id=..., **_extra)`。
3. 退出码：`0` 完成 / `1` 失败 / `2` 熔断 / `3` 等审批或等人工 / `4` 用户中断。
4. **8 个阶段里只有 stage2 有公开的 `build_task(cfg, proj)`**。其余的任务构造是内部函数且签名各异：
   - stage1：`build_setting_task(proj, manifest_path, normalized_dir, ...)`；
     碎片另有 `prepare_scraps` / `build_scraps_task`
   - stage3：`build_batch_task(cfg, proj, chapters, global_outline_path, setting_path)`
   - stage4：章 / 段群级任务（在 `stage4_writing` 内部构造，与 `parts_state` 的群切分绑定）
   - stage5：`build_check_task(proj, raw_dir, setting_path, checked_dir, batch_files, batch_index, total_batches)`
   - stage6：`build_polish_task(proj, checked_dir, refined_dir, chapters, vol_index=None, ...)`
   - stage7 / stage8：**纯本地**（Word / Markdown 导出），不需要模型 → self 模式下照常执行
5. 阶段完成判定依赖盘上产物（`utils/stage_artifacts.missing_artifacts` +
   orchestrator 的 `artifact_gate`），不是“调用成功”本身 —— 这一点对 self 极其有利：
   **产物齐了就能直接判 done**。

## 方案：两步，不要一次改完

### 第一步：统一任务书产出接口（可单独发布）

给每个需要模型的阶段加一个**只读、零 token** 的入口：

```python
def emit_tasks(cfg, proj, progress, task_dir="data/state/tasks/self", **_extra) -> dict:
    """产出本阶段要外部执行的任务书。返回
    {"stage": n, "tasks": [{"path", "target", "check"}], "hint": "..."}
    - path   任务书文件（含目标产物路径 / 合格判据 / 检查命令）
    - target 执行完必须出现的产物（相对路径）
    - check  执行方自检命令（通常是 nfctl status / 对应阶段脚本）
    """
```

实现纪律：

- **不改现有 `build_*_task` 的签名与内容**，只在外面包一层：调用它拿正文 →
  追加“交回协议”头（目标路径 / 合格判据 / 检查命令）→ 写进 `emit_tasks` 的目录。
- 任务书必须**自包含**（与 hermes 任务书同标准）：设定集 / 大纲 / 前文全部内联或给出绝对路径。
- stage4 的任务粒度**必须复用现有的群切分函数**（`stage4_writing` 里那套），
  否则“执行完第 3 章”无法被 `parts` 记账识别；一章一份任务书是安全默认。
- stage5 的任务书必须写清批次账本（`_verify_ledger`）的要求，否则外部执行完
  会因为“没有 ledger”被判失败。若决定 self 下跳过 ledger，必须在任务书与
  progress 里**同时注明跳过**（不能悄悄放宽）。

判据（每条都要能变红）：

- T1 每个需要模型的阶段（1-6）都产出 ≥1 份任务书
- T2 每份任务书含目标产物路径 + 合格判据 + 检查命令
- T3 任务书内容与 `build_*_task` 的产物一致（去掉协议头后逐字相同）
- T4 零 LLM 调用（用 FakeClient 断言 `run_task` 从未被调用）

### 第二步：orchestrator 的 self 分支

```python
if engine == "self":
    # 1) 产物齐全 → 标 done，继续下一阶段（复用 artifact_gate 的判据）
    # 2) 否则 → 调 STAGES[n].emit_tasks(...)
    #     progress.set_stage(n, "awaiting_external", tasks=[...路径...])
    #     print 指引（去哪里执行、执行完重跑哪条命令）
    #     return 3
```

配套：

- `nf_api._rc_to_result`：退出码 3 且阶段状态为 `awaiting_external` →
  文案“任务书已生成（N 份），执行完重跑即可”（与“等待审批”区分开）。
- `/state` 的 `stages[].status` 会带 `awaiting_external` → 前端 `statusBadge`
  必须映射成“等外部执行”，否则会显示原始英文。
- 重跑语义：任务书已存在且产物仍缺 → **重新生成**（内容与旧的一致，幂等覆盖），
  并在 progress 记 `task_hint`（最后一版任务书路径）。

判据：

- S1 `engine=self` + 空产物 → 生成任务书、退出 3、progress 标 `awaiting_external`
- S2 任务书在、产物缺 → 重跑仍退出 3，且任务书内容一致（幂等）
- S3 产物补齐 → 该阶段直接 done，不进下一阶段的任务书生成
- S4 `direct` / `hermes` 行为逐字不变（回归靠既有 207 条用例 + FakeClient 端到端）

## 明确不做（避免范围失控）

- **不做**“外部执行状态回传”协议（如 webhook）：self 的判据是**产物**，
  不是“执行方报告成功”。报告会撒谎，产物不会。
- **不做**自动唤醒外部 agent：谁来执行是调用方的决定（同样是“人拍板”的边界）。
- stage7 / stage8 在 self 下照常本地执行，不产任务书（它们本来就不调模型）。

## 预估

- 第一步：6 个阶段 × 30~60 行 + 单测，约 400 行；1 个专注会话。
- 第二步：orchestrator ~60 行 + nf_api ~10 行 + 前端 badge ~5 行 + 端到端用例。
- 风险点只有两个：stage4 的群切分对齐、stage5 的 ledger 要求。开工先读这两处。
