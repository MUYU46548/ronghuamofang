# 命令速查（完整表）

> 本表原先直接写在 AGENTS.md 里 —— 但它有 30 KB / 90+ 行，把手册顶到了 64 KB 的
> **指令加载预算**之上：超限部分会被截断，等于后面的《硬性约束》《能力边界》对
> 自动加载的助手**不可见**（手册失去作用，且没有任何报错）。2026-10-03 迁到此处，
> AGENTS.md 只留最常用的几条 + 指向本文件。
>
> 约定同 AGENTS.md：workdir=项目根，一律用项目 .venv 的 python。

## 全部命令

| 目的 | 命令（workdir=项目根，用 .venv python） |
|------|------|
| **Agent 只读入口（全景/自检）** | `python scripts/nfctl.py status`（一屏：书名/阶段/进度/成本/待审批/产物）· `check`（环境自检，含 **YAML 重复键检测** + **提示词模板体检**：必需清单从代码里 `load_template()` 的**调用点**反推，缺文件/空文件/调用名漏 `.md` 都算阻塞）· `doctor`（**数据一致性自检**：runs 脏行/产物完整性/孤儿文件/成本负数）· `test`（**统一测试运行器**：跑 tests/unit + tests/http 全部自定义测试，`--pattern` 选单个）· **`release-check`（发版前必跑：质量门 + 全量测试 + MCP 真机握手 + e2e 视觉验收 + 打包产物核验，一条命令；`--skip-e2e` 可跳）** · `api <GET路径>`（只读转发 nf_api，省手写 curl）· **`model-check [provider] [--live]`（换模型 / 换供应商前必跑**：配置完整性、Key 脱敏回显、各角色模型是否在该 provider 的 `available_models` 内、fallback 是否跨供应商（跨了必 404）、白名单覆盖面；`--live` 才发一次最小请求，默认零 token）· `serve`（启动零依赖调试看板 127.0.0.1:8766）。**只读、零 token**；写操作走下方各脚本 |
| 全流程启动 | `python scripts/orchestrator.py` |
| 从阶段 N 重跑 | `python scripts/orchestrator.py --from N` |
| 只跑阶段 N | `python scripts/orchestrator.py --stage N` |
| **详细调试模式** | `python scripts/orchestrator.py --verbose`（打印配置全景 + LLM 请求/响应原文落盘 `data/state/llm_raw/`） |
| **执行计划预演** | `python scripts/orchestrator.py --dry-run`（零 token 预演：打印将执行哪些阶段/会在哪个审批门停下，不调 LLM、不写文件、不建 runs 记录；判定逻辑与实跑一致） |
| 审批阶段 N | `python scripts/approve.py --stage N`（**阶段 1 审批 = 设定集定稿 → 自动冻结 canon 快照** `data/setting/canon.json`；之后 stage2/3 只读快照，改素材不再扰动已定稿大纲。`--revoke` 会撤掉快照退回活稿） |
| **素材状态 / 墓碑（A3，确定性零 token）** | `python scripts/material_review.py [--materials 目录] [--no-state]`（体检报告追加「素材状态」段：条目 status + 分歧分级 + 墓碑）· `--conflicts`（只列**需人工裁决**的分歧，有则退出码非零）· `--reject "名字" --reason "…"`（登记墓碑：该条目已否决，归并时**不得复活**）· `--unreject "名字"`。审计明细落 `data/setting/merge_audit.json`，墓碑落 `data/setting/tombstones.json`。判据是**确定性**的：同名字段两边都非空且字面不同 → `review`（报人）；一方是另一方子串/低风险字段（英文名·别名）/「待填充」→ `auto`（自动归并） |
| 撤销审批 | `python scripts/approve.py --stage N --revoke` |
| 打回阶段 N | `python scripts/reject.py --stage N "原因"`（记录原因+清理下游产物+重置状态+撤销审批；`--dry-run` 预演） |
| 大纲体检 | `python scripts/outline_review.py`（确定性，标出空泛节点；默认附**与上一版对比**，`--no-compare` 关闭） |
| 大纲迭代趋势 | `python scripts/outline_review.py --trend [--history 目录]`（确定性零 token：逐版本指标表 + **收敛判断** done/improving/stalled/mixed/insufficient，回答「还要不要再迭代一轮」） |
| 设定体检（stage1后自动） | `python scripts/material_review.py`（确定性，**类型感知**：先按 `utils/setting_schema.is_character` 分开人物/非人物，再对人物标碎片/缺失维度，报告 data/setting/material_review.md） |
| 从设定库导入设定 | `python scripts/obsidian_integrate.py scan [--vault 路径] [--output data/setting/setting.json]`（⚠️ **整体替换**语义：vault 内容覆盖现有设定集，`plot_fragments`/`timeline` 会清空；**覆盖前自动备份**到 `data/setting/history/setting_vN.json`。目录约定见 `obsidian_bridge.scan_vault` docstring） |
| 扫描设定库（只读） | `python scripts/obsidian_bridge.py scan [--vault 路径]`（只扫不写，落 `data/state/obsidian_index.json`）；`config` 查看当前 vault/沙盒配置 |
| 正典一致性检查 | `python scripts/obsidian_bridge.py check <文件>`（已实现：**疑似新角色**（启发式）+ **locked 违例**（确定性）；⚠️ `conflicts` **仍未实现**（需语义判断）。输出会显式标注；`checked=false` 表示**检查未生效**，不等于通过） |
| **locked 条目检查（确定性，零 token）** | `python scripts/obsidian_bridge.py locked [--setting 路径]`（vault 里 `locked=true` 的条目是否在最终设定集中 **缺失 / 丢了锁定标记 / 被改名**。**不判语义冲突** —— 机器判不准，硬做只会变噪音。vault 无 locked 条目或设定集未生成时明确回报"没查"） |
| 沙盒查看/推送 | `python scripts/obsidian_bridge.py list` / `push <文件> [--subdir X]`（vault 只读，产物只写沙盒；默认 `data/state/obsidian_sandbox/`，可在 `config/system.yaml` 的 `obsidian.sandbox_dir` 改为你的库内目录） |
| **vault ↔ 设定集 内容级对账（A4）** | `python scripts/obsidian_bridge.py diff [--setting 路径] [--json]`（一次报全四类差异：**缺失**（vault 有、设定集没有 → canon 被归并吞掉）/ **新增**（设定集有、vault 没有 → 人工确认是否回写）/ **改名** / **丢 locked**。零 token，复用 `locked` 的同一份匹配判据。⚠️ `checked=false` = **没查**（vault 未配/设定集未生成），不等于一致；有「缺失/改名/丢 locked」时退出码非零） |
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
| Obsidian 后处理（完书后） | `python scripts/obsidian_postprocess.py [--books\|--role-records\|--roles "角色甲,角色乙"] [--dry-run] [--no-llm]`（作品介绍页/出场记录/新角色设定草稿 → 沙盒 `data/state/obsidian_sandbox/`，可用 `config/system.yaml` 的 `obsidian.sandbox_dir` 指到你库内收件目录） |
| 角色出场统计 | `python scripts/appearances.py`（确定性，输出 data/state/appearances.json，obsidian_postprocess 自动调用） |
| 润色后体检 | `python scripts/polish_review.py`（确定性，交付 Word 前跑） |
| 校对（stage 5.5，交付 Word 前） | `python scripts/proofread.py [--scope refined] [--llm] [--dry-run]`（确定性：标点/错字/格式/章节节奏，**零 token**；`--llm` 追加语义校对。报告 data/outline/proofread_report.json + .md） |
| 生成前 token/费用预估 | `python scripts/estimate_tokens.py [--stage 4] [--json] [--no-history] [--verbose]`（历史实测均值优先，无历史则字符折算；GUI 运行前确认框走 `GET /estimate`。**同时给出 token 闸门对照**：已用 + 本轮估算 = 跑完预计，撞闸门会明确提示 —— hermes 下 ¥ 恒 0，这才是有意义的预估） |
| 拆书 / 章节节奏 | `python scripts/book_split.py --input <文本文件> [--emit] [--json] [--list-patterns]`（切章模式自动识别 → data/state/book_pacing.json；`--emit` 另导出切分正文到 data/state/book_split/。**输入文件只读**） |
| 成本报告 | `python scripts/cost_report.py`（总览，含**阶段2 初版 vs 迭代**细分）；`--by-chapter`（分章）；`--by-outline`（**大纲逐轮费用**：v0 初版 + 每轮迭代 + 累计 + 平均）；`--runs 5` |
| 多书切换 | `python scripts/switch_book.py --list` / `--archive` / `--restore "书名"`（均需 `--yes`）。归档范围 = `data/` 的 8 项产物 **+ `config/` 与 `materials/`**（2026-10-01 起：此前换书会丢配置与素材卡）。`system.yaml`/`system.local.yaml` 属**应用级**，归档后自动复制回工作区；`project.yaml` 重置为空白骨架，恢复时被书档版本覆盖 |
| **副产物管理台（看清 / 导出 / 清理）** | `python scripts/artifacts.py`（**一屏看清**十来处副产物：路径 / 文件数 / 体积 / 最老最新 / **安全等级** / 是什么 / 怎么复用）· `--json`（机器可读，给外部 Agent）· `--export <id,…\|all> --to <目录>`（默认打包成 zip，**不动源文件**）· `--clean <id,…> [--older-than 天] [--keep-last N] --yes`（**默认 dry-run**，与 `snapshot.py --restore` 同惯例）。等级三档：`safe`（截断稿/失败审稿原文/`--verbose` 原文/任务文件/拆书切分 → 随时可清）、`backup`（config、prompts、大纲、章节、设定集的历史版本 → 按天/按量收口）、**`keep`（快照 `history/`、归档书 `data/books/`、报告、待审沙盒、账本 `logs/runs.db` → 一律拒绝清理并指出该用哪个工具）**。自检：`python tests/unit/test_artifacts.py`（31 断言，临时根上跑，含 keep 拒绝 / dry-run 不删 / `--yes` 真删 / zip 内容与源文件未变） |
| 快照恢复 | `python scripts/snapshot.py --restore <ID>`（**默认 dry-run 预览**，加 `--yes` 执行；恢复前自动打 `pre_restore` 折返点；`--delete-extra` 才删快照外文件） |
| 素材预扫描 | `python scripts/stage1_consolidate.py` |
| 原始碎片聚类预览 | `python scripts/utils/scrap_cluster.py`（自由命名碎片 → 内容聚类 + 时间序 + 前瞻备忘；`--json` 机器可读、`--dir` 换目录） |
| 本地 API 服务（GUI 化 P0） | `python scripts/nf_api.py`（默认 127.0.0.1:8765；`--port/--host` 可调；`--allow-fake` 为无 LLM 测试模式） |
| 提示词模板编辑（GUI） | 控制台「设置」页签 → 提示词模板面板；底层 `GET /prompts/list`、`GET /prompts/get?name=`、`POST /prompts/save`（白名单 `prompts/stage[1-7]_*.md`，禁止 `../`；保存自动备份 `prompts/history/`） |
| 文风特征自检 | `python tests/unit/test_style_v2.py`（**19 断言**：边界输入（None/空/空白/极短/无标点/纯标点）不炸也不硬凑 · 旧键向后兼容 · v2 新增键丰富度 · 抒情段 vs 口语段的特征与**风格指令都不同** · 对白比例真的把两类分开。⚠️ 它 2026-10-03 之前是**纯 print 的临时脚本**（`print("旧键齐全:", ...)` → False 也退 0）） |
| **测试文件卫生（结构性护栏）** | `python tests/unit/test_runner_hygiene.py`（**8 断言**：`tests/unit`+`tests/http` 每个用例都必须有**判定构造**（`check(`/`ok(`/`assert`/计数器/清单）+ **非零退出路径** + **失败必须驱动退出码**（不是无条件 `exit(0)`、也不是"只打印状态"）+ 判定数 ≥3；含**反证**（造一个纯 print 用例必须被抓）+ e2e 的 print 型基线只许降。判据会先剥掉 docstring/注释 —— 否则"文档里举的坏例子"会被当成坏代码） |
| 风格偏差自检 | `python tests/unit/test_style_drift.py`（compute_style_drift 阈值/边界 + stage6 报告追加集成） |
| API 验收自测 | `python scripts/nf_api_selftest.py`（⚠️ 清空 data/ 与 logs/ 后以 fake 模式起服务跑全链用例；会销毁当前书档产物，history/ 快照保留。碎片写入类端点不在其中——见下） |
| 碎片聚类自检 | `python tests/unit/test_scrap_cluster.py`（自由命名 / 时间戳回退 / 内容聚类 / 否定语境 / 指纹稳定性，44 断言） |
| **会话续接（B2，默认关）** | 开关 `config/system.yaml` 的 `hermes.session_continuation`（默认 **false**）。开启后 stage4 逐章写作经 `hermes chat --resume` 接**上一章**的子会话（不再每章冷启动）；只有**成功的章节**才把 session 传下去；续接失败**自动降级**为全新会话重试一次（`resumed_fallback: true`）。⚠️ 这是**行为变更**（agent 上下文跨章累积），真实试写尚无收益实测证据 —— 首次启用建议先跑一章，看过产物质量与用量再决定。自检：`python tests/unit/test_session_continuation.py`（20 断言，含「所有 `run_task` 实现必须收 `session_id`」的防漏改护栏） |
| **canon 新鲜度 + 陪跑/自主（B4）** | 自检：`python tests/unit/test_canon_refresh.py`（22 断言）。**陪跑/自主** = `gates.material_autonomy`，**只有显式写下该键才生效**（键缺失 = 保持改动前行为，老配置与测试替身不受影响）：`false`（本机默认，**陪跑**）= stage1 归并后**停在审批门**等作者拍板；`true`（**自主**）= 不额外停。**canon 重冻**：stage1 收尾时若 canon 已存在，按「旧 canon ↔ 当前设定集」条目级 diff 重冻并打印**变更字段**（只报「哪些键变了」，**不报「谁改的」** —— 单人本地无从得知）。⚠️ 首轮**不自动造 canon**（定稿必须走 `approve.py --stage 1`，自动冻结会把机器归并冒充人工定稿） |
| **设定集状态（B3，外部 Agent 三薄工具）** | `GET /setting/conflicts`（素材冲突清单，零 token）· `GET /setting/canon[?full=1]`（canon 元信息，默认**不含全文**）· `POST /setting/status {name, action: adopt\|reject\|uncertain, reason?}`（人工拍板：`reject` 必填 reason 且**先写墓碑**、不依赖条目存在；`adopt/uncertain` 需条目已存在。人工结果带 `status_source=human`，**压过**后续机器判定）。MCP 侧 = `nf_get_setting_conflicts` / `nf_get_canon` / `nf_set_material_status`。自检：`python tests/http/test_setting_state_api.py`（**真起 nf_api** 跑 28 断言；⚠️ `checked=false` = **没查**，不等于一致） |
| **stage1 进料节流（B1）** | `python tests/unit/test_stage1_intake.py`（多回合迭代时只把**未定稿**素材喂给归并：adopted 条目原文不进 prompt、改由 canon 快照承担；uncertain 与新增照旧进。⚠️ **节流前提是 canon 存在** —— canon 缺失一律不节流，否则剔除 adopted 等于丢上下文。含防回归护栏「body 里不得残留目录引用」：素材正文进 prompt 的机制是 `llm_client.inline_inputs` 对**目录**做 glob 全量内联，模板里留着目录引用就等于节流零效果） |
| stage1 碎片集成自检 | `python tests/unit/test_stage1_scraps.py`（在**临时工作目录**跑 fake 全链，真实 data/ 零污染；含"改碎片必触发重归并"） |
| 碎片端点 HTTP 自检 | `python tests/http/test_scraps_api_http.py`（临时项目根起 nf_api，覆盖 save/delete/promote 等写入端点与确认门，真实仓库零触碰） |
| 质量自评闭环自检 | `python tests/unit/test_auto_rewrite.py`（临时工作目录跑 fake：目标收集/兜底重扫/dry-run/幂等/轮次上限/预算熔断/审稿发现叠加，43 断言） |
| 自动重写端点 HTTP 自检 | `python tests/http/test_auto_rewrite_api_http.py`（临时项目根起 nf_api：`/state` 暴露字段 + `POST /auto_rewrite/run` 默认 dry-run/显式执行/幂等，17 断言） |
| 思考模型兼容自检 | `python tests/unit/test_thinking_compat.py`（离线单测 + 真实 TokenHub 探针：确认「不关思考 content 空 / 关闭思考 content 非空 / reasoning_effort 被拒」；`--offline` 跳过真实调用。34 断言） |
| 日志轮转自检 | `python tests/unit/test_run_log.py`（临时 LOCALAPPDATA 沙箱：超期归档/清理白名单/级别过滤/配置解包顺序/dry-run/坏配置不抛/年龄下限不为负，38 断言） |
| 快照恢复自检 | `python tests/unit/test_snapshot_restore.py`（临时 CWD：dry-run 零改动、真实恢复、pre_restore 折返点、extras 递归检测、范围限定、路径护栏，30 断言） |
| **MCP 暴露面（禁止端点永不外露）** | `python tests/unit/test_mcp_approval_surface.py`（**9 断言**，零网络：`nf_mcp.MCP_TOOLS` 里**任何**工具都不得指向 `agent_guard.FORBIDDEN_IN_AGENT_MODE` 的端点（含 `/config/token_limit` —— 外部 Agent 不得抬高自己的闸门）· 工具名里不许出现禁止语义（防改名绕过）· `nf_mcp` 不硬编码禁止清单（单一事实来源在 `agent_guard`）· 每个 HTTP 工具的端点必须在 `nf_api` 分发链里真实存在（防死工具）· **数量不写死**但设下限，以 `MCP_TOOLS` 为唯一事实来源） |
| **设定自动补全闭环自检** | `python tests/unit/test_setting_refine_auto.py`（在**临时项目根**跑 fake，零真实调用：达标即停**零 LLM 调用**短路 / 一轮达标即停 / 无进展即停 / 轮次上限 / 默认关 / dry-run / 缺设定集可行动报错 / CLI>gates 优先级 / **定向 feedback 真的写进了 LLM 任务文件**（端到端）/ **orchestrator 钩子签名可绑定**（AST + `inspect.signature().bind()`，防被 `except` 吞掉的静默降级），66 断言） |
| **大纲迭代闭环自检** | `python tests/unit/test_outline_iteration.py`（临时项目根，零 LLM：版本对比三分支（改善/停滞/退化）+ 逐条目差分 / 趋势序列与收敛五分支出对 / `--trend` CLI / **精修成本真的写进 cost_log**（stage=2 + chapter=版本号）+ 未记账时明确标注 / `cost_report --by-outline` 初版与迭代分离，52 断言，含 6 组反向验证） |
| **沙盒审核 + 回写 + 开工建议自检** | `python tests/unit/test_sandbox_review_and_advisor.py`（临时项目根，零 LLM：登记/通过/驳回/未登记报错 · **改过的文件必须重新审核**（sha256）· 孤儿检测 · `write_sandbox` 自动登记 · 审核队列 CLI · 导出审核包（3 份待审 + **global.md 未被改动**）· 重复导出保留审核状态 · 建议方案池与优先级 · entry_id 可执行 · 零 token 保证，78 断言，含 8 组反向验证） |
| **obsidian 联动 + 大纲精修完整性** | `python tests/unit/test_obsidian_integrity.py`（临时项目根，零 LLM：扫描无跨类目污染/无重复 / 导入前自动备份 / 返回类型一致 / KB 注入不静默 / `check_consistency` 诚实化 + 召回修复 / **`locked_violations` 已实现**（从 unimplemented 移入 implemented）/ **大纲精修不在体检 FAIL 时假成功**，44 断言，含 7 组反向验证） |
| **locked 违例检查自检** | `python tests/unit/test_locked_violations.py`（零 LLM：`missing`/`lock_lost`/`renamed` 三判据各自**正反例** + `locked: "true"` 字符串不误报 + 别名形态不算改名 + **未生效时必须回报 `checked=false`**（不许把没查伪装成通过），14 断言） |
| **多书归档范围自检** | `python tests/unit/test_switch_book_scope.py`（**临时项目根真实归档/恢复**：`config/` 与 `materials/` 随书走 · 应用级配置留在工作区 · `project.yaml` 重置为骨架 · **老格式归档恢复不得删掉工作区配置**（逐项合并而非整体替换），21 断言） |
| **模型/供应商切换通道自检** | `python scripts/nfctl.py model-check [provider]`（离线：base_url/key 完整性（**Key 只输出前 3 后 4**）、各角色模型是否在该 provider 清单内、fallback 是否跨供应商、白名单覆盖面；`--live` 才发最小请求） |
| **stage4 长跑止损自检** | `python tests/unit/test_stage4_stoploss.py`（纯 fake：连续失败 3 章即停（**只跑 3 章不是 10 章**）· 阈值 0 = 关闭 · **章间预算熔断**真的在阶段内生效 · 反证：正常路径不受影响，16 断言） |
| **续跑产物有效性判据自检** | `python tests/unit/test_resume_output_judgement.py`（13 断言：空壳必须判不可用、正常产物不误杀、阈值边界；并断言 **stage5/6 的调用点真的用了 `is_usable_output`**，防退回裸 `exists()` —— "修好了又漂回去"光测函数测不到） |
| **第二轮审查修复回归** | `python tests/unit/test_audit_fixes_20261001.py`（22 断言：**缓存 token 不得双重计价**（数值断言，旧口径 1.18 → 新 0.28）· **行尾「……」不得当截断**（否则章节反复重写烧钱）· `merge_book` 逐章取优不丢章 · `progress.json` 顶层非对象不崩 · 结构性源码断言：reject 清理范围 / `build_state.agent_mode` / MCP 阶段上限 7 / orchestrator `_post_stage` 调用点 / 前端 `only_stage:false`） |
| **段落级精修轴** | `python tests/unit/test_paragraph_refine.py`（零 LLM：char_diff/summarize_diff/风格特征/split_paragraphs/历史/回退，24 断言） |
| **正文退化检测自检** | `python tests/unit/test_verify_degenerate.py`（审计行动项 10：6 种退化形态（复读填充/思考残片/元话语拒答/无段落换行/标点灌水/模板骨架）各**判据隔离**样本 + 真实章节零误报 + 阈值边界 + `is_chapter_complete` 集成 + **7 条反向验证**（删判据→必须变绿），59 断言） |
| 审稿闭环端点 HTTP 自检 | `python tests/http/test_review_api_http.py`（临时项目根起 nf_api：报告缺失 → 400 可行动提示、审查 job、决策保存与回读、批量精修真读到决策、键值格式兼容、交互式被拒，25 断言） |
| stage4 出场同步自检 | `python tests/unit/test_stage4_appearances.py`（临时工作目录跑 fake stage4：appearances.json 自动生成、幂等不翻倍、异常注入不阻断，22 断言） |
| 校对自检 | `python tests/unit/test_proofread.py`（临时项目根：四类确定性检查命中 + 误报防护 + 节奏离群 + 报告双落盘 + LLM 分支走 FakeClient，46 断言） |
| 新端点 HTTP 自检 | `python tests/http/test_new_endpoints_api_http.py`（临时项目根起 nf_api：/estimate 四种取参口径、proofread 报告缺失可行动 + 运行后落盘、style/analyze 四种 source、book/pacing 与 book/split、/models/add 与 /models/switch、/export/markdown、/outline/chapters/save、**/outline/trend**、**/outline/advise**、**/outline/trend**、**/outline/advise**、**/sandbox/queue**、**/sandbox/file**（只读预览 + 路径遍历/绝对路径双拒）、**POST /sandbox/review**（通过/驳回/退回 · 驳回必带 note · 只动状态库不动文件），110 断言） |
| GUI↔API 契约核对 | `python tests/e2e/test_gui_api_contract.py`（**AST 解析**：Vue 里每个 `api("…")` 都能在 nf_api 找到**同方法**分支；do_GET/do_POST 名遮蔽 AST 检查；死分支、丢失 elif 守卫（结构判据）、分支链长度回归；每个主题都要有 CSS 变量块，35 断言） |
| **失败路径回归（F1~F8 + S9/S10）** | `python tests/unit/test_failure_paths.py`（**主动把系统打坏**：重试计数/异常不外泄/预算熔断/用户停止/stage4 逐章失败隔离/静态门禁/seedWorkspace 升级/审批打回清下游/阶段键集对齐/末阶段熔断，69 断言。用 `utils/failing_client.py` 造可控失败，真实 data/ 零污染） |
| UX 端点 HTTP 自检 | `python tests/http/test_ux_flow_api_http.py`（临时项目根起 nf_api：`/logs/tail` 无文件/混编码/lines 边界、`/stage/skip` 的 confirm 与 stage 护栏、跳过落盘与 `/state` 回读、跳过→打回清标记、`/review/comment` 回归，26 断言） |
| UX 真机视觉验收 | `python tests/e2e/e2e_ux_verify.py`（Playwright 打开构建产物：一键工作流条、错误恢复条、跳过确认框、命令面板 Ctrl+K、快捷键 Space/R/数字/?、暗色主题对比度、**关于弹窗 / 项目页签 / 新建项目向导三步 / 冷启动引导 / 止烧阈值面板 / 流水线条 token 闸门 / 设置页侧边栏分类 / 主题颜色亮暗分排 / 接入其他 Agent 配置段 / 让它自装第三渠道 / 诊断与调试（含 0.6.7 新增的「出厂内容」盘点与清理入口）**，**125 断言** + 截图 `Temp/gui_verify/ux/`。需先起 8091 静态服务 + `tests/e2e/mock_nf_api_state.py --port 8798` + `--port 8797 --cold`（冷启动状态）+ `scripts/nf_api.py --port 8799 --allow-fake`。⚠️ 8799 打的是**真实工作区**：期望值必须从 `/state` 推导（不许写死阶段号），且**下一步是审批门时绝不按 Space**（Space = 执行下一步，会真的把审批按掉 —— 2026-10-03 实测踩到并已还原）） |
| 项目向导/关于端点自检 | `python tests/http/test_project_wizard_api_http.py`（临时项目根起 nf_api：`/config/style_notes` 回归 ImportError、单行↔多行反复改写不写坏 YAML、`/project/create` 参数护栏与「有数据不归档则拒绝」、归档+重建+写 project.yaml 全链路、`/project/init` 真写盘、`/about` 字段，44 断言） |
| 出厂清单 / 样例检测与清理（纯逻辑） | `python tests/unit/test_factory_manifest.py`（sha256 与播种记录、清单 write/read/合并与回退不倒退、样例检测「2 个一致 + 1 个改过 + 1 个无关 → 只报 2 个」、清理只动匹配/保相对路径/`materials/` 外拒绝/幂等，42 断言；**夹具一律临时目录，绝不对真实仓库 materials/ 下手**） |
| 出厂端点 HTTP 自检 | `python tests/http/test_factory_api_http.py`（临时项目根起 nf_api：`GET /factory/list` 形状 + **零副作用**（盘点前后逐字节不变）、缺 `confirm` 400 且不落地、`confirm` 后真移进 `data/books/_trash/factory__*/` 且回读命中归零、改过/无关/progress.json 不动、`/factory/clean` 在 `FORBIDDEN_IN_AGENT_MODE` 内而 `/factory/list` 不在，26 断言） |
| 真机截图 + 控制台报错检查 | `node tests/e2e/cdp_shots_new_tabs.js <http://127.0.0.1:8090> <出图目录>`（CDP 驱动 headless Chrome，逐页签截图 + 抓 console error/warning + 抓非 2xx 响应 URL。先起 nf_api:8765 与构建产物的静态服务；Node 22 自带 WebSocket，无需额外依赖） |
| **质量门禁（提交前必跑）** | `python scripts/quality_gate.py`（**未定义名 + 语法错零容忍**：`undefined name` 与 `compile()` 失败一律阻塞，退出码 1；其余历史告警只计数不阻塞。`--changed` 只查 git 变更文件，`--list-warn` 打印完整告警）。⚠️ 语法错**必须**阻塞：一个连 import 都跑不起来的仓库被盖绿灯时，全量测试会集体报 ImportError、真因被埋（2026-10-03 亲历） |
| **泄露门禁（发布前必跑 / CI 自动）** | `python scripts/leak_scan.py --list ci/blacklist.txt [--json]`（扫**被跟踪文件**里的私人词：真名 / 作品词 / 本机路径 / 私人 vault 目录结构。**fail-closed**：词表缺失·为空·条目 <20 条 → exit 2；**非二进制文件读不到也算失败**（没扫到 ≠ 通过）。退出码 0 零命中 / 1 有命中或读不到 / 2 词表不可用）。词表：本机 `ci/blacklist.txt`（已 gitignore）+ CI secret `LEAK_BLACKLIST`；仓库里只有零真值的 `ci/blacklist.example`。**替换映射表**（真值 ↔ 虚构值）在私有的 `ci/fictional-map.txt`（已 gitignore）——它同时是词表损坏时的容灾重建来源。路径豁免写在词表里（`allow: LICENSE # 理由`）且**会被打印出来** —— 静默豁免等于开后门。⚠️ 取文件列表必须 `git -c core.quotepath=false ls-files`：默认会转义非 ASCII 文件名，导致中文名文件被**静默跳过**（本仓实测少报过 ~60 处） |
| **发布树卫生** | `config/project.yaml.example` 是模板（`project.yaml` 发布后不再随仓库分发，缺文件会让新克隆首跑崩 —— `load_project_yaml` 没有兜底）；CI 的 `leak-gate` 任务同时拦「运行产物被跟踪」（`logs/`·`data/`·`output/`·`history/`·`materials/raw/*.md`） |
| kb / 模型端点契约自检 | `python tests/http/test_kb_and_models_api_http.py`（vault 未配置 → /kb/* 给可行动 400；白名单两级校验与 strict=false 逃生门，27 断言） |
| 重试路径回归自检 | `python tests/unit/test_orchestrator_retry.py`（S1 回归：阶段失败不得抛异常、runs 不得留 running 脏行、finish_run_if_running 幂等，13 断言） |
| 流式跑阶段回归自检 | `python tests/unit/test_stream_stage.py`（S2 回归：流式/非流式共用退出码翻译、无平行分支，18 断言） |
| 配置泄露 / 白名单回路自检 | `python tests/unit/test_config_and_models.py`（S3/S8 回归：无本机绝对路径泄露、白名单两级校验接通，29 断言） |
| 设定集 schema 归一自检 | `python tests/unit/test_setting_schema.py`（两种 schema 都能读全 + 大纲解析 + 别名/id 匹配 + **实体类型判定** + 去重 + **真实归档快照回归**，70 断言） |

