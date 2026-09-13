# NovelForge — 项目操作手册（AGENTS.md）

本文件在 AI 助手于本项目目录工作时自动加载。用户在本项目发起任务时，按本手册执行。

## 项目定位

NovelForge（对外品牌名：**绒花墨坊** / `ronghuamofang`）是全自动长篇小说生成系统：用户放置素材 → 系统调度流水线 → 交付 Word 成品。
用户只做两件事：**放置素材** + **说"运行 NovelForge 项目"**。其余由系统自主执行，用户保留审批权。

**执行引擎可切换**：`config/system.yaml` 的 `engine` 字段控制。`direct`=OpenAI 兼容直连（当前接 TokenHub，可随时换供应商），`hermes`=Hermes 子会话（备选）。所有调用点经 `make_client(cfg, role)` 取客户端，零调用点硬编码引擎。

## 启用流程（用户说"运行 NovelForge"时）

1. 前置检查：
   - `config/project.yaml` 是否已填书名/类型/章节数（书名仍为"示例书名（待填写）"时提醒用户）
   - `materials/raw/` 是否有素材（为空时提醒用户，不要空跑）
2. 启动：`terminal(command="python scripts/orchestrator.py", background=true, notify_on_complete=true, workdir="E:/CODE/CangKu/NovelForge")`
   - 必须用项目 .venv 的 python：`E:/CODE/CangKu/NovelForge/.venv/Scripts/python.exe`
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

## 命令速查

| 目的 | 命令（workdir=项目根，用 .venv python） |
|------|------|
| 全流程启动 | `python scripts/orchestrator.py` |
| 从阶段 N 重跑 | `python scripts/orchestrator.py --from N` |
| 只跑阶段 N | `python scripts/orchestrator.py --stage N` |
| 审批阶段 N | `python scripts/approve.py --stage N` |
| 撤销审批 | `python scripts/approve.py --stage N --revoke` |
| 打回阶段 N | `python scripts/reject.py --stage N "原因"`（记录原因+清理下游产物+重置状态+撤销审批；`--dry-run` 预演） |
| 大纲体检 | `python scripts/outline_review.py`（确定性，标出空泛节点） |
| 设定体检（stage1后自动） | `python scripts/material_review.py`（确定性，标出碎片角色/缺失维度，报告 data/setting/material_review.md） |
| 设定补全（审批前） | `python scripts/setting_refine.py "意见"` 或 `--auto-thin`（仅从素材推断+llm_inferred 标记，备份 data/setting/history/） |
| 大纲精修（定向修订） | `python scripts/refine_outline.py "意见"`（`--dry-run` 只生成任务不跑子会话） |
| 章节精修（定向修订） | `python scripts/refine_chapter.py 3 "意见"`（备份 data/chapters/history/，±20% 铁律） |
| 全书摘要（完书后） | `python scripts/book_summary.py`（输出 output/{书名}_全书摘要.md，可粘贴 ROSA） |
| ROSA 后处理（完书后） | `python scripts/rosa_postprocess.py [--books\|--role-records\|--roles "露汐,小林"] [--dry-run] [--no-llm]`（作品介绍页/出场记录/新角色设定草稿 → Obsidian_AI_Sandbox/10_Inbox/） |
| 角色出场统计 | `python scripts/appearances.py`（确定性，输出 data/state/appearances.json，rosa_postprocess 自动调用） |
| 润色后体检 | `python scripts/polish_review.py`（确定性，交付 Word 前跑） |
| 成本报告 | `python scripts/cost_report.py`（总览）；`--by-chapter`（分章）；`--runs 5` |
| 多书切换 | `python scripts/switch_book.py --list` / `--archive` / `--restore "书名"`（归档 data/books/，均需 `--yes`） |
| 项目快照 | `python scripts/snapshot.py "标签"`；查看 `--list`（orchestrator 每阶段成功后自动快照） |
| 素材预扫描 | `python scripts/stage1_consolidate.py` |
| 原始碎片聚类预览 | `python scripts/utils/scrap_cluster.py`（自由命名碎片 → 内容聚类 + 时间序 + 前瞻备忘；`--json` 机器可读、`--dir` 换目录） |
| 本地 API 服务（GUI 化 P0） | `python scripts/nf_api.py`（默认 127.0.0.1:8765；`--port/--host` 可调；`--allow-fake` 为无 LLM 测试模式） |
| 提示词模板编辑（GUI） | 控制台「设置」页签 → 提示词模板面板；底层 `GET /prompts/list`、`GET /prompts/get?name=`、`POST /prompts/save`（白名单 `prompts/stage[1-7]_*.md`，禁止 `../`；保存自动备份 `prompts/history/`） |
| 文风特征自检 | `python Temp/test_style_v2.py`（对范文跑 extract_style_features + build_style_instruction，含空/短文本边界） |
| 风格偏差自检 | `python Temp/test_style_drift.py`（compute_style_drift 阈值/边界 + stage6 报告追加集成） |
| API 验收自测 | `python scripts/nf_api_selftest.py`（⚠️ 清空 data/ 与 logs/ 后以 fake 模式起服务跑全链用例；会销毁当前书档产物，history/ 快照保留。碎片写入类端点不在其中——见下） |
| 碎片聚类自检 | `python Temp/test_scrap_cluster.py`（自由命名 / 时间戳回退 / 内容聚类 / 否定语境 / 指纹稳定性，44 断言） |
| stage1 碎片集成自检 | `python Temp/test_stage1_scraps.py`（在**临时工作目录**跑 fake 全链，真实 data/ 零污染；含"改碎片必触发重归并"） |
| 碎片端点 HTTP 自检 | `python Temp/test_scraps_api_http.py`（临时项目根起 nf_api，覆盖 save/delete/promote 等写入端点与确认门，真实仓库零触碰） |

## 关键路径

- 设定集：`data/setting/setting.json`（四顶层键 characters/world/plot_fragments/timeline）
- 整体大纲：`data/outline/global.md`（审批门对象）
- 大纲体检报告：`data/outline/review_report.md`（outline_review.py 生成）
- 素材体检报告：`data/setting/material_review.md`（material_review.py 生成，stage1 后自动）
- 设定补全历史：`data/setting/history/`（setting_refine.py 每次补全自动备份 setting_vN.json）
- 角色出场统计：`data/state/appearances.json`（appearances.py 生成，rosa_postprocess 自动调用）
- 风格偏差报告：追加在 `data/outline/polish_report.md`（分卷为 `polish_report_volN.md`）末尾，
  stage6 每卷润色后自动写入；配置 `book.style_reference` 才生成，阈值 30%，仅供参考不阻断流程
- 大纲修订历史：`data/outline/history/`（每次精修自动备份 global_vN.md，可回退）
- 用户大纲输入：`config/project.yaml` 的 `book.user_outline`（可选；提供后 stage2/精修优先遵循）
- Word 成品模板：`templates/*.dotx`（config 的 `book.word_template` 指定；.dotx 自动转换；替换 [书籍标题]/[作者]/[目录占位符]/[请输入文本] 占位符；更换模板只改配置或覆盖 templates/）
- 项目快照：`history/{时间戳}_{标签}/`（每阶段成功后自动生成，保留最近 10 份；data/ 不进 git，快照承担版本职责）
- 多书归档：`data/books/{书名}/`（switch_book.py 归档/恢复；切换前 orchestrator 会提示书名不一致）
- 章节修订历史：`data/chapters/history/`（refine_chapter.py 每次精修备份 chNN_vM.md）
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
- **预算熔断（exit=2）**：查询 `SELECT SUM(cost_yuan) FROM cost_log` → 与用户确认是否调高 `config/system.yaml` 的 `budget.limit_yuan` → 清 `data/state/progress.json` 的 `budget.paused` → 重跑
- **用户打回**：`python scripts/reject.py --stage N "原因"`（记录原因、清理 N 及下游产物、重置状态、撤销审批；history/ 备份保留可回退）→ `--from N` 重跑。打回 2 用精修通道（refine_outline）而非 reject
- **素材更新**：新增素材后 `--from 1` 重跑（已有章节文件自动跳过，不会重写）
- **提示词调优**：直接改 `prompts/stageN_*.md`（模板与代码分离，无需改脚本）

## 硬性约束

- **ROSA 设定库只读**：永不写入 `E:/图书馆/ROSA`；系统仅通过 `materials/` 与项目内 `data/` 工作
- `data/`、`history/` 不进 Git（由 history/ 快照承担版本职责）；`prompts/`、`config/`、`scripts/` 进 Git
- **用户私人数据不进 Git**：`materials/original_scraps/`（及 `_backup/`）永不随源码仓库走，后续由独立的
  导入/导出功能承担迁移；碎片→卡片落盘一律由 Python 执行（模型写入白名单只有 `data/**`），且覆盖前先备份
- **模型凭据**：项目 `.env`（不入 git）承载 LLM API Key（`TOKENHUB_API_KEY`）；engine=hermes 时凭据由 Hermes 统一管理。切换引擎/模型/服务商只改 `config/system.yaml`（`engine` / `model.*` / `providers`），新 provider 计价须先补 `scripts/utils/cost_tracker.py` 的 `RATES`（可用 `scripts/price_wizard.py` 交互式更新）。**模型白名单纪律：用户免费体验包按模型领取，未经用户确认不得指定/更换付费模型**
- **直连引擎（engine: direct）语义**：无子会话工具循环——任务文件的输入文件段由 `llm_client.inline_inputs` 全文内联进单请求；多文件输出任务自动按目标文件拆分请求；模型产物经 `===FILE/APPEND/DELETE===` 协议落盘（白名单：`data/**` 与 `logs/runs.db`），期望外路径直接拒绝
- **子会话（engine: hermes）**：任务文件（data/state/tasks/）必须自包含全部上下文
- **本地 API（nf_api.py）**：函数级复用 orchestrator/approve/reject/refine，不经过 Hermes 子进程；客户端注入走 `_client_for_env`（默认 make_client 真引擎，`NF_API_ALLOW_FAKE=1`/`--allow-fake` 时注入 FakeClient——**仅限测试**，自测脚本运行会清空 data/ 运行产物）；服务默认只绑 127.0.0.1
- 破坏性操作前必须先 `snapshot.py` + 征求用户确认（删除章节产物、删除原始碎片、覆盖写已存在的素材卡）
