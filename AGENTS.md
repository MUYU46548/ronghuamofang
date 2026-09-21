# NovelForge — 项目操作手册（AGENTS.md）

本文件在 AI 助手于本项目目录工作时自动加载。用户在本项目发起任务时，按本手册执行。

## 项目定位

NovelForge（对外品牌名：**绒花墨坊** / `ronghuamofang`）是半自动长篇小说生成系统：机器包办苦力（素材归并→大纲→写作→检查→润色→Word），用户保留审批权和最终精修权。
用户只做两件事：**放置素材** + **说"运行 NovelForge 项目"**。其余由系统自动执行，关键节点设审批门暂停等待人工确认。

**执行引擎可切换**：`config/system.yaml` 的 `engine` 字段控制。`direct`=OpenAI 兼容直连（当前接 TokenHub，可随时换供应商），`hermes`=Hermes 子会话（备选）。所有调用点经 `make_client(cfg, role)` 取客户端，零调用点硬编码引擎。

**思考模式兼容（2026-09-14 起）**：glm-5.x / kimi 等模型默认进 thinking 模式时，正文会全进 `reasoning_content`、`content` 为空（且思考 token 吃掉 `max_tokens` 预算）→ 流水线产出空白。
防线有三：① `providers.<id>.disable_thinking_models` 名单内模型自动注入「关闭思考」片段（候选顺序见 `disable_thinking_payloads`，TokenHub 实测首选 `thinking: {type: disabled}`；`reasoning_effort: none` 会被该平台拒收）；② 未列名单的思考模型由客户端自动探测并注入后重试；③ 全部失败才从 `reasoning_content` 兜底提取。
**换供应商只改 `config/system.yaml` 的这两个键，不要改代码、不要在 `llm_client.py` 里硬编码模型名。**

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
| 大纲体检 | `python scripts/outline_review.py`（确定性，标出空泛节点；默认附**与上一版对比**，`--no-compare` 关闭） |
| 大纲迭代趋势 | `python scripts/outline_review.py --trend [--history 目录]`（确定性零 token：逐版本指标表 + **收敛判断** done/improving/stalled/mixed/insufficient，回答「还要不要再迭代一轮」） |
| 设定体检（stage1后自动） | `python scripts/material_review.py`（确定性，**类型感知**：先按 `utils/setting_schema.is_character` 分开人物/非人物，再对人物标碎片/缺失维度，报告 data/setting/material_review.md） |
| 从设定库导入设定 | `python scripts/obsidian_integrate.py scan [--vault 路径] [--output data/setting/setting.json]`（⚠️ **整体替换**语义：vault 内容覆盖现有设定集，`plot_fragments`/`timeline` 会清空；**覆盖前自动备份**到 `data/setting/history/setting_vN.json`。目录约定见 `obsidian_bridge.scan_vault` docstring） |
| 扫描设定库（只读） | `python scripts/obsidian_bridge.py scan [--vault 路径]`（只扫不写，落 `data/state/obsidian_index.json`）；`config` 查看当前 vault/沙盒配置 |
| 正典一致性检查 | `python scripts/obsidian_bridge.py check <文件>`（⚠️ **仅「疑似新角色」一项已实现**，`conflicts`/`locked_violations` **未实现**，输出会显式标注；空结果 ≠ 无问题） |
| 沙盒查看/推送 | `python scripts/obsidian_bridge.py list` / `push <文件> [--subdir X]`（vault 只读，产物只写沙盒；默认 `data/state/obsidian_sandbox/`，可在 `config/system.yaml` 的 `obsidian.sandbox_dir` 改为你的库内目录） |
| **沙盒审核队列** | `python scripts/sandbox_review.py --queue`（待审）· `--list`（全部+状态）· `--approve <路径>` · `--reject <路径> --note "原因"` · `--orphans`（未登记文件）· `--json`。**改过的文件会自动重置为待审**（内容 sha256 比对）；状态存 `data/state/sandbox_manifest.json`，不写进产物 frontmatter（避免带进 vault）。**GUI 等价物：「审核」页签**（`GET /sandbox/queue` + `GET /sandbox/file` 预览 + `POST /sandbox/review`） |
| **大纲迭代 → 沙盒审核包** | `python scripts/outline_export.py [--dry-run] [--trend-window 5]`（导出 3 份待审产物：对比稿 / 当前大纲全文 / 迭代趋势。**只写沙盒，绝不改 global.md**） |
| **开工方向建议** | `python scripts/outline_advisor.py [--window 3] [--json]`（确定性零 token：按严重度给出候选方案 + 依据 + 目标条目 + **可执行 next_step** + 调用次数与取舍。推荐规则：结构问题 > 退化 > 有 THIN > 停滞 > 开工） |
| 设定补全（审批前） | `python scripts/setting_refine.py "意见"`（仅从素材推断+llm_inferred 标记，备份 data/setting/history/） |
| 设定自动补全（闭环） | `python scripts/setting_refine.py --auto-thin [--max-rounds N] [--dry-run]`（按体检 THIN 清单**定向**补全：点名角色+缺失维度；**达标即停/无进展即停/轮次上限**；每轮独立备份；orchestrator 由 `gates.setting_refine_auto` 触发，**默认关**，上限取 `gates.setting_refine_max_rounds`） |
| 大纲精修（定向修订） | `python scripts/refine_outline.py "意见"`（`--dry-run` 只生成任务不跑子会话） |
| 章节精修（定向修订） | `python scripts/refine_chapter.py 3 "意见"`（备份 data/chapters/history/，±20% 铁律） |
| 低分章自动重写（方向3） | `python scripts/auto_rewrite.py [--threshold 6] [--chapters 3,7] [--max-rounds 1] [--dry-run]`（复用 batch_refine 的备份/铁律/提示词；orchestrator 由 `gates.auto_rewrite` 触发，**默认关**） |
| 章节审查（审稿闭环 Phase 1） | `python scripts/chapter_review.py [--scope raw\|checked\|refined] [--report data/outline/review_report.json] [--dry-run]`（产出 review_report.json/.md；GUI「审稿」页签等价于 `POST /review/run`） |
| 批量精修（审稿闭环 Phase 2） | `python scripts/batch_refine.py --report data/outline/review_report.json [--decisions file\|interactive] [--auto] [--dry-run]`；`--decisions file` 读同目录 `review_report.decisions.json`（格式 `{"decisions":[{finding_id,chapter,action,feedback}]}`，action=accept/ignore；GUI 保存的即此格式） |
| 全书摘要（完书后） | `python scripts/book_summary.py`（输出 output/{书名}_全书摘要.md，可粘贴 Obsidian） |
| Obsidian 后处理（完书后） | `python scripts/obsidian_postprocess.py [--books\|--role-records\|--roles "露汐,小林"] [--dry-run] [--no-llm]`（作品介绍页/出场记录/新角色设定草稿 → Obsidian_AI_Sandbox/10_Inbox/） |
| 角色出场统计 | `python scripts/appearances.py`（确定性，输出 data/state/appearances.json，obsidian_postprocess 自动调用） |
| 润色后体检 | `python scripts/polish_review.py`（确定性，交付 Word 前跑） |
| 校对（stage 5.5，交付 Word 前） | `python scripts/proofread.py [--scope refined] [--llm] [--dry-run]`（确定性：标点/错字/格式/章节节奏，**零 token**；`--llm` 追加语义校对。报告 data/outline/proofread_report.json + .md） |
| 生成前 token/费用预估 | `python scripts/estimate_tokens.py [--stage 4] [--json] [--no-history] [--verbose]`（历史实测均值优先，无历史则字符折算；GUI 运行前确认框走 `GET /estimate`） |
| 拆书 / 章节节奏 | `python scripts/book_split.py --input <文本文件> [--emit] [--json] [--list-patterns]`（切章模式自动识别 → data/state/book_pacing.json；`--emit` 另导出切分正文到 data/state/book_split/。**输入文件只读**） |
| 成本报告 | `python scripts/cost_report.py`（总览，含**阶段2 初版 vs 迭代**细分）；`--by-chapter`（分章）；`--by-outline`（**大纲逐轮费用**：v0 初版 + 每轮迭代 + 累计 + 平均）；`--runs 5` |
| 多书切换 | `python scripts/switch_book.py --list` / `--archive` / `--restore "书名"`（归档 data/books/，均需 `--yes`） |
| 项目快照 | `python scripts/snapshot.py "标签"`；查看 `--list`（orchestrator 每阶段成功后自动快照） |
| 快照恢复 | `python scripts/snapshot.py --restore <ID>`（**默认 dry-run 预览**，加 `--yes` 执行；恢复前自动打 `pre_restore` 折返点；`--delete-extra` 才删快照外文件） |
| 素材预扫描 | `python scripts/stage1_consolidate.py` |
| 原始碎片聚类预览 | `python scripts/utils/scrap_cluster.py`（自由命名碎片 → 内容聚类 + 时间序 + 前瞻备忘；`--json` 机器可读、`--dir` 换目录） |
| 本地 API 服务（GUI 化 P0） | `python scripts/nf_api.py`（默认 127.0.0.1:8765；`--port/--host` 可调；`--allow-fake` 为无 LLM 测试模式） |
| 提示词模板编辑（GUI） | 控制台「设置」页签 → 提示词模板面板；底层 `GET /prompts/list`、`GET /prompts/get?name=`、`POST /prompts/save`（白名单 `prompts/stage[1-7]_*.md`，禁止 `../`；保存自动备份 `prompts/history/`） |
| 文风特征自检 | `python tests/unit/test_style_v2.py`（对范文跑 extract_style_features + build_style_instruction，含空/短文本边界） |
| 风格偏差自检 | `python tests/unit/test_style_drift.py`（compute_style_drift 阈值/边界 + stage6 报告追加集成） |
| API 验收自测 | `python scripts/nf_api_selftest.py`（⚠️ 清空 data/ 与 logs/ 后以 fake 模式起服务跑全链用例；会销毁当前书档产物，history/ 快照保留。碎片写入类端点不在其中——见下） |
| 碎片聚类自检 | `python tests/unit/test_scrap_cluster.py`（自由命名 / 时间戳回退 / 内容聚类 / 否定语境 / 指纹稳定性，44 断言） |
| stage1 碎片集成自检 | `python tests/unit/test_stage1_scraps.py`（在**临时工作目录**跑 fake 全链，真实 data/ 零污染；含"改碎片必触发重归并"） |
| 碎片端点 HTTP 自检 | `python tests/http/test_scraps_api_http.py`（临时项目根起 nf_api，覆盖 save/delete/promote 等写入端点与确认门，真实仓库零触碰） |
| 质量自评闭环自检 | `python tests/unit/test_auto_rewrite.py`（临时工作目录跑 fake：目标收集/兜底重扫/dry-run/幂等/轮次上限/预算熔断/审稿发现叠加，43 断言） |
| 自动重写端点 HTTP 自检 | `python tests/http/test_auto_rewrite_api_http.py`（临时项目根起 nf_api：`/state` 暴露字段 + `POST /auto_rewrite/run` 默认 dry-run/显式执行/幂等，17 断言） |
| 思考模型兼容自检 | `python tests/unit/test_thinking_compat.py`（离线单测 + 真实 TokenHub 探针：确认「不关思考 content 空 / 关闭思考 content 非空 / reasoning_effort 被拒」；`--offline` 跳过真实调用。34 断言） |
| 日志轮转自检 | `python tests/unit/test_run_log.py`（临时 LOCALAPPDATA 沙箱：超期归档/清理白名单/级别过滤/配置解包顺序/dry-run/坏配置不抛/年龄下限不为负，38 断言） |
| 快照恢复自检 | `python tests/unit/test_snapshot_restore.py`（临时 CWD：dry-run 零改动、真实恢复、pre_restore 折返点、extras 递归检测、范围限定、路径护栏，30 断言） |
| 域模块拆分护栏 | `python tests/unit/test_api_domains.py`（域模块契约 + 薄转发形态 + 依赖方向 + **`__main__` 别名守卫** + 无相对路径 IO，25 断言） |
| **设定自动补全闭环自检** | `python tests/unit/test_setting_refine_auto.py`（在**临时项目根**跑 fake，零真实调用：达标即停**零 LLM 调用**短路 / 一轮达标即停 / 无进展即停 / 轮次上限 / 默认关 / dry-run / 缺设定集可行动报错 / CLI>gates 优先级 / **定向 feedback 真的写进了 LLM 任务文件**（端到端）/ **orchestrator 钩子签名可绑定**（AST + `inspect.signature().bind()`，防被 `except` 吞掉的静默降级），66 断言） |
| **大纲迭代闭环自检** | `python tests/unit/test_outline_iteration.py`（临时项目根，零 LLM：版本对比三分支（改善/停滞/退化）+ 逐条目差分 / 趋势序列与收敛五分支出对 / `--trend` CLI / **精修成本真的写进 cost_log**（stage=2 + chapter=版本号）+ 未记账时明确标注 / `cost_report --by-outline` 初版与迭代分离，52 断言，含 6 组反向验证） |
| **沙盒审核 + 回写 + 开工建议自检** | `python tests/unit/test_sandbox_review_and_advisor.py`（临时项目根，零 LLM：登记/通过/驳回/未登记报错 · **改过的文件必须重新审核**（sha256）· 孤儿检测 · `write_sandbox` 自动登记 · 审核队列 CLI · 导出审核包（3 份待审 + **global.md 未被改动**）· 重复导出保留审核状态 · 建议方案池与优先级 · entry_id 可执行 · 零 token 保证，78 断言，含 8 组反向验证） |
| **obsidian 联动 + 大纲精修完整性** | `python tests/unit/test_obsidian_integrity.py`（临时项目根，零 LLM：扫描无跨类目污染/无重复 / 导入前自动备份 / 返回类型一致 / KB 注入不静默 / `check_consistency` 诚实化 + 召回修复 / **大纲精修不在体检 FAIL 时假成功**，42 断言，含 7 组反向验证） |
| **正文退化检测自检** | `python tests/unit/test_verify_degenerate.py`（审计行动项 10：6 种退化形态（复读填充/思考残片/元话语拒答/无段落换行/标点灌水/模板骨架）各**判据隔离**样本 + 真实章节零误报 + 阈值边界 + `is_chapter_complete` 集成 + **7 条反向验证**（删判据→必须变绿），59 断言） |
| 审稿闭环端点 HTTP 自检 | `python tests/http/test_review_api_http.py`（临时项目根起 nf_api：报告缺失 → 400 可行动提示、审查 job、决策保存与回读、批量精修真读到决策、键值格式兼容、交互式被拒，25 断言） |
| stage4 出场同步自检 | `python tests/unit/test_stage4_appearances.py`（临时工作目录跑 fake stage4：appearances.json 自动生成、幂等不翻倍、异常注入不阻断，22 断言） |
| 校对自检 | `python tests/unit/test_proofread.py`（临时项目根：四类确定性检查命中 + 误报防护 + 节奏离群 + 报告双落盘 + LLM 分支走 FakeClient，46 断言） |
| 新端点 HTTP 自检 | `python tests/http/test_new_endpoints_api_http.py`（临时项目根起 nf_api：/estimate 四种取参口径、proofread 报告缺失可行动 + 运行后落盘、style/analyze 四种 source、book/pacing 与 book/split、/models/add 与 /models/switch、/export/markdown、/outline/chapters/save、**/outline/trend**、**/outline/advise**、**/outline/trend**、**/outline/advise**、**/sandbox/queue**、**/sandbox/file**（只读预览 + 路径遍历/绝对路径双拒）、**POST /sandbox/review**（通过/驳回/退回 · 驳回必带 note · 只动状态库不动文件），110 断言） |
| GUI↔API 契约核对 | `python tests/e2e/test_gui_api_contract.py`（**AST 解析**：Vue 里每个 `api("…")` 都能在 nf_api 找到**同方法**分支；do_GET/do_POST 名遮蔽 AST 检查；死分支、丢失 elif 守卫（结构判据）、分支链长度回归；每个主题都要有 CSS 变量块，35 断言） |
| **失败路径回归（F1~F8 + S9/S10）** | `python tests/unit/test_failure_paths.py`（**主动把系统打坏**：重试计数/异常不外泄/预算熔断/用户停止/stage4 逐章失败隔离/静态门禁/seedWorkspace 升级/审批打回清下游/阶段键集对齐/末阶段熔断，69 断言。用 `utils/failing_client.py` 造可控失败，真实 data/ 零污染） |
| UX 端点 HTTP 自检 | `python tests/http/test_ux_flow_api_http.py`（临时项目根起 nf_api：`/logs/tail` 无文件/混编码/lines 边界、`/stage/skip` 的 confirm 与 stage 护栏、跳过落盘与 `/state` 回读、跳过→打回清标记、`/review/comment` 回归，26 断言） |
| UX 真机视觉验收 | `python tests/e2e/e2e_ux_verify.py`（Playwright 打开构建产物：一键工作流条、错误恢复条、跳过确认框、命令面板 Ctrl+K、快捷键 Space/R/数字/?、暗色主题对比度、**关于弹窗 / 项目页签 / 新建项目向导三步 / 冷启动引导**，64 断言 + 截图 `Temp/gui_verify/ux/`。需先起 8091 静态服务 + `tests/e2e/mock_nf_api_state.py --port 8798` + `--port 8797 --cold`（冷启动状态）+ `scripts/nf_api.py --port 8799 --allow-fake`） |
| 项目向导/关于端点自检 | `python tests/http/test_project_wizard_api_http.py`（临时项目根起 nf_api：`/config/style_notes` 回归 ImportError、单行↔多行反复改写不写坏 YAML、`/project/create` 参数护栏与「有数据不归档则拒绝」、归档+重建+写 project.yaml 全链路、`/project/init` 真写盘、`/about` 字段，44 断言） |
| 真机截图 + 控制台报错检查 | `node tests/e2e/cdp_shots_new_tabs.js <http://127.0.0.1:8090> <出图目录>`（CDP 驱动 headless Chrome，逐页签截图 + 抓 console error/warning + 抓非 2xx 响应 URL。先起 nf_api:8765 与构建产物的静态服务；Node 22 自带 WebSocket，无需额外依赖） |
| **质量门禁（提交前必跑）** | `python scripts/quality_gate.py`（**未定义名零容忍**：`undefined name` 一律阻塞，退出码 1；其余历史告警只计数不阻塞。`--changed` 只查 git 变更文件，`--list-warn` 打印完整告警） |
| kb / 模型端点契约自检 | `python tests/http/test_kb_and_models_api_http.py`（vault 未配置 → /kb/* 给可行动 400；白名单两级校验与 strict=false 逃生门，27 断言） |
| 重试路径回归自检 | `python tests/unit/test_orchestrator_retry.py`（S1 回归：阶段失败不得抛异常、runs 不得留 running 脏行、finish_run_if_running 幂等，13 断言） |
| 流式跑阶段回归自检 | `python tests/unit/test_stream_stage.py`（S2 回归：流式/非流式共用退出码翻译、无平行分支，18 断言） |
| 配置泄露 / 白名单回路自检 | `python tests/unit/test_config_and_models.py`（S3/S8 回归：无本机绝对路径泄露、白名单两级校验接通，29 断言） |
| 设定集 schema 归一自检 | `python tests/unit/test_setting_schema.py`（两种 schema 都能读全 + 大纲解析 + 别名/id 匹配 + **实体类型判定** + 去重 + **真实 ROSA 快照回归**，70 断言） |

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
- **预算熔断（exit=2）**：查询 `SELECT SUM(cost_yuan) FROM cost_log` → 与用户确认是否调高 `config/system.yaml` 的 `budget.limit_yuan` → 清 `data/state/progress.json` 的 `budget.paused` → 重跑
- **用户打回**：`python scripts/reject.py --stage N "原因"`（记录原因、清理 N 及下游产物、重置状态、撤销审批；history/ 备份保留可回退）→ `--from N` 重跑。打回 2 用精修通道（refine_outline）而非 reject
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
