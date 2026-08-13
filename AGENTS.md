# NovelForge — Hermes 操作手册（AGENTS.md）

本文件在 Hermes 于本项目目录工作时自动加载。用户在本项目发起任务时，按本手册执行。

## 项目定位

NovelForge 是全自动长篇小说生成系统：用户放置素材 → Hermes 调度流水线 → 交付 Word 成品。
用户只做两件事：**放置素材** + **说"运行 NovelForge 项目"**。其余由系统自主执行，用户保留审批权。

## 启用流程（用户说"运行 NovelForge"时）

1. 前置检查：
   - `config/project.yaml` 是否已填书名/类型/章节数（书名仍为"示例书名（待填写）"时提醒用户）
   - `materials/raw/` 是否有素材（为空时提醒用户，不要空跑）
2. 启动：`terminal(command="python scripts/orchestrator.py", background=true, notify_on_complete=true, workdir="E:/CODE/CangKu/NovelForge")`
   - 必须用项目 .venv 的 python：`E:/CODE/CangKu/NovelForge/.venv/Scripts/python.exe`
   - 长任务（数小时），用后台运行 + 完成通知
3. 监控：轮询 `logs/runs.db` 与 `data/state/progress.json` 向用户汇报进度/成本
4. 退出码语义（orchestrator 返回）：
   - `0` = 全部完成；`1` = 阶段失败暂停；`2` = 预算熔断；`3` = 等待阶段 2 审批
5. 审批门：exit=3 时，提示用户审阅 `data/outline/global.md`：
   - 审阅前先跑 `python scripts/outline_review.py` 看体检报告（标出空泛节点/章节规划缺漏），避免草草开工
   - 用户确认 → `python scripts/approve.py --stage 2` → 重新运行 orchestrator（断点续跑）
   - 用户不满意 → 让用户说明意见，跑 `python scripts/refine_outline.py "意见"`（增量修订，自动备份旧版到 `data/outline/history/`）→ 反复至满意 → 再审批

## 命令速查

| 目的 | 命令（workdir=项目根，用 .venv python） |
|------|------|
| 全流程启动 | `python scripts/orchestrator.py` |
| 从阶段 N 重跑 | `python scripts/orchestrator.py --from N` |
| 只跑阶段 N | `python scripts/orchestrator.py --stage N` |
| 审批阶段 N | `python scripts/approve.py --stage N` |
| 撤销审批 | `python scripts/approve.py --stage N --revoke` |
| 大纲体检 | `python scripts/outline_review.py`（确定性，标出空泛节点） |
| 大纲精修（定向修订） | `python scripts/refine_outline.py "意见"`（`--dry-run` 只生成任务不跑子会话） |
| 素材预扫描 | `python scripts/stage1_consolidate.py` |

## 关键路径

- 设定集：`data/setting/setting.json`（四顶层键 characters/world/plot_fragments/timeline）
- 整体大纲：`data/outline/global.md`（审批门对象）
- 大纲体检报告：`data/outline/review_report.md`（outline_review.py 生成）
- 大纲修订历史：`data/outline/history/`（每次精修自动备份 global_vN.md，可回退）
- 用户大纲输入：`config/project.yaml` 的 `book.user_outline`（可选；提供后 stage2/精修优先遵循）
- 逐章大纲：`data/outline/chapters/NN.md`
- 章节：`data/chapters/raw`（原稿）/ `checked`（检查后）/ `refined`（润色后）
- 滚动摘要：`data/summaries/rolling.md`（全书摘要 + 近 5 章）
- 进度：`data/state/progress.json`；运行记录：`logs/runs.db`
- 最终成品：`output/{书名}_完整版.docx`

## 运维场景

- **阶段失败（exit=1）**：读 orchestrator 输出定位失败阶段 → 检查原因（校验失败/子会话异常）→ 修 `prompts/` 模板或素材 → 重跑 `--from N`
- **预算熔断（exit=2）**：查询 `SELECT SUM(cost_yuan) FROM cost_log` → 与用户确认是否调高 `config/system.yaml` 的 `budget.limit_yuan` → 清 `data/state/progress.json` 的 `budget.paused` → 重跑
- **用户打回**：定位目标阶段 → 清空其下游产物（如 `data/chapters/refined/`、`data/chapters/checked/`）→ `--from N` 重跑
- **素材更新**：新增素材后 `--from 1` 重跑（已有章节文件自动跳过，不会重写）
- **提示词调优**：直接改 `prompts/stageN_*.md`（模板与代码分离，无需改脚本）

## 硬性约束

- **ROSA 设定库只读**：永不写入 `E:/图书馆/ROSA`；系统仅通过 `materials/` 与项目内 `data/` 工作
- `data/`、`history/` 不进 Git（由 history/ 快照承担版本职责）；`prompts/`、`config/`、`scripts/` 进 Git
- 模型凭据由 Hermes 统一管理（~/.hermes/.env），项目 `.env` 仅承载可选第三方密钥
- 子会话（hermes chat -q）为全新会话，任务文件（data/state/tasks/）必须自包含全部上下文
- 破坏性操作（删除章节产物）前必须先征求用户确认
