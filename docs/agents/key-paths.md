# 关键路径

> 原 `AGENTS.md` 的一节，2026-10-27 拆分至此（原文件 76,326 字节，超过 65,536 字节的
> 指令加载预算会被系统**静默截断**，被截掉的恰是靠后的《能力边界》《硬性约束》）。
> **纪律效力不变**：动这块代码/数据前按需读本文。

- 设定集：`data/setting/setting.json`（四顶层键 characters/world/plot_fragments/timeline）
- 整体大纲：`data/outline/global.md`（审批门对象）
- 大纲体检报告：`data/outline/review_report.md`（outline_review.py 生成）
- 素材体检报告：`data/setting/material_review.md`（material_review.py 生成，stage1 后自动）
- 设定补全历史：`data/setting/history/`（setting_refine.py 每次补全自动备份 setting_vN.json）
- 角色出场统计：`data/state/appearances.json`（appearances.py / `refresh_appearances()` 生成，**stage4 每章通过校验后自动刷新**，obsidian_postprocess 自动调用；同章重复同步不重复计数）
- 风格偏差报告：追加在 `data/outline/polish_report.md` 末尾（分卷模式先写 `polish_report_volN.md`，
  stage6 收尾自动**合并覆盖**成 `polish_report.md`——GUI 产物概况与「润色体检报告」按钮只认汇总名，2026-10-17 修），
  stage6 每卷润色后自动写入；配置 `book.style_reference` 才生成，阈值 30%，仅供参考不阻断流程
- 大纲修订历史：`data/outline/history/`（每次精修自动备份 global_vN.md，可回退）
- 用户大纲输入：`config/project.yaml` 的 `book.user_outline`（可选；提供后 stage2/精修优先遵循）
- Word 成品模板：`templates/*.dotx`（config 的 `book.word_template` 指定；.dotx 自动转换；替换 [书籍标题]/[作者]/[目录占位符]/[请输入文本] 占位符；更换模板只改配置或覆盖 templates/）
  - **GUI 入口（件五，2026-10-11）**：设置 →「Word 模板」= 下拉列 `templates/` 下 .dotx/.docx + 打开模板目录 + 恢复默认；写入走 `POST /config/word_template {template|reset}`（`project_config.set_book_fields` 定向改写 + 备份 + 回读校验），读取走 `GET /config/word_template`（清单 + 当前值 + 出厂默认）。**面向用户的入口一律 GUI**（用户 2026-10-10：「用户永远不会用终端」），CLI 直改该键仍是 agent 通道
- 项目快照：`history/{时间戳}_{标签}/`（每阶段成功后自动生成，保留最近 10 份；data/ 不进 git，快照承担版本职责）
  - **恢复**：`python scripts/snapshot.py --restore <ID> --yes`。默认 dry-run；
    恢复前自动存 `pre_restore` 快照作折返点；快照外的文件默认保留。
- 多书归档：`data/books/{书名}/`（switch_book.py 归档/恢复；切换前 orchestrator 会提示书名不一致）
  - **查看 / 删除**：`GET /project/archive/tree|file`（只读）、`POST /project/archive/delete`
    （GUI 来源头 + 逐字书名确认；默认移入下条的回收站，`purge=true` 才真删）
  - 归档回收站：`data/books/_trash/{书名}__{时间戳}/`（删除的默认落点，要彻底清除手工删该目录；
    `list_books()` 跳过它，不会以「一本叫 _trash 的书」出现在列表里）
  - **出厂样例回收站**：`data/books/_trash/factory__{时间戳}/`（`POST /factory/clean` 的落点，
    保留原相对路径、可手工捞回；同上不会出现在书列表）
- 出厂清单：`data/state/factory_manifest.json`（seedWorkspace 实际复制文件后写，合并语义：
  `{seed_version, generated_at, files:{相对路径: sha256}}`）；盘点/清理端点 `GET /factory/list`
  （零副作用）与 `POST /factory/clean {confirm:true}`，GUI 入口在「设置 → 诊断与调试 → 出厂内容」，
  比对真源是随包 `examples/`（源码态 `ROOT/examples`，打包态 `NF_SAMPLE_ROOT` 注入）
- **样例书的大纲与素材必须配套**（2026-10-18）：`examples/sample-book/` 的 `user_outline`
  讲沈砚/程砧/机关术，`materials/` 的 19 张卡讲**同一套设定**。改其中任一侧后跑
  `tests/unit/test_sample_book_coherence.py`（判据：大纲专有名词在卡里都有落点、
  旧宇宙词不残留、脱敏声明不丢）。⚠️ 历史教训：2026-10-03 虚构化把素材卡整套换成
  另一个宇宙而 `user_outline` 未同步 → 拿样例书试跑时设定集 87% 与大纲无关，
  成品崩坏而**全程绿灯**（此前无任何判据检查二者配套）
- **二号测试用例**（2026-10-19）：`tests/fixtures/sample-book-2/`（《炭城纪》）是
  **第三个虚构宇宙**的完整素材集（19 张卡 + `project.yaml`），与出厂样例并列但
  **不进安装包、不进 GUI**。判据 `tests/unit/test_sample_book_2_coherence.py`（11 断言：
  卡数 / 大纲实体落点 / 前两个宇宙零词重叠 / 脱敏声明 / 文件名与专名一致）。
  它存在的意义是**换一整套专名，判据照样成立** —— 一套夹具证明不了流水线不过拟合
- 章节修订历史：`data/chapters/history/`（refine_chapter.py 每次精修备份 chNN_vM.md）
- 单章字数区间：`config/system.yaml` 的 `chapter.target_words`（**列表** `[low, high]`，出厂 [5000,9000]；
  `GET/POST /config/target_words` 读写，GUI 设置页同源。判据与边界见「止烧与安全闸门」节）
- 中文标点归一化：唯一实现 `scripts/utils/text_norm.py`，7 处挂载点（stage4/5/6 落盘后、
  refine_chapter / refine_paragraph 落盘后、stage7 合并兜底）。判据与护栏见专节
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
- **长跑保护**（2026-10-01）：`gates.stage4_max_consecutive_failures`（默认 3，0=关）——
  stage4 **连续** N 章失败即判定系统性问题（模型挂了/提示词改坏/输入构造错）并**提前停止**，
  避免"坏掉了还一路跑完 100 章"在 `data/chapters/raw/` 铺满废稿；已完成的章节不受影响。
  另：stage4/5/6 现在**在阶段内部**也查预算熔断（此前只在阶段之间查 —— 而一章就能烧掉几元，
  限额对"一次跑几十章"这种最该保护的情形恰好失效）
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
