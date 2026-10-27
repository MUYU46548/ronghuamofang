# 运维场景

> 原 `AGENTS.md` 的一节，2026-10-27 拆分至此（原文件 76,326 字节，超过 65,536 字节的
> 指令加载预算会被系统**静默截断**，被截掉的恰是靠后的《能力边界》《硬性约束》）。
> **纪律效力不变**：动这块代码/数据前按需读本文。

- **阶段失败（exit=1）**：读 orchestrator 输出定位失败阶段 → 检查原因（校验失败/子会话异常）→ 修 `prompts/` 模板或素材 → 重跑 `--from N`
- **熔断暂停（exit=2）**：先看 orchestrator 打出的 `熔断暂停[原因]`——
  `原因=tokens_total` → 调 `config/system.yaml` 的 `budget.token_limit.max_total_tokens`；
  `原因=yuan` → 查 `SELECT SUM(cost_yuan) FROM cost_log` 并与用户确认是否调高 `budget.limit_yuan`
  （**engine: hermes 下金额恒 0，不可能命中**）。改完清 `data/state/progress.json` 的
  `budget.paused` → `orchestrator.py --from N` 续跑（已完成阶段不重跑）。
- **用户打回**：`python scripts/reject.py --stage N "原因"`（记录原因、清理 N 及下游产物、重置状态、撤销审批；history/ 备份保留可回退）→ `--from N` 重跑。打回 2 用精修通道（refine_outline）而非 reject
- **GUI 排障（2026-10-06 对齐方寸）**：顶栏「📋 日志」常驻；设置 → 诊断与调试
  （运行形态/健康态/代码根/数据根/日志路径 + 查看日志/打开日志文件/复制诊断信息/
  DevTools 开关（Ctrl+Shift+I 或 F12）/重启后端 API；重启在流水线运行中禁用）。
- **素材更新**：新增素材后 `--from 1` 重跑（已有章节文件自动跳过，不会重写）
- **跨版本升级 / 书稿迁移（C3，2026-10-14）**：三样东西三个版本 —— 程序本体（安装包版本）、
  代码与提示词（`SEED_VERSION` ↔ 工作区 `.seed-version`，`seedWorkspace` 启动时 force 刷新，
  `config/` 只补缺不覆盖）、书稿数据（`DATA_SCHEMA` ↔ `data/state/workspace_schema.json`，
  `utils/workspace_migrate` 启动时对齐）。数据侧六条契约：判据只有一份（CLI / nf_api /
  orchestrator 都调 `ensure()`）· **数据比代码新 = 拒绝运行** · 先快照后动手（丙3，快照失败
  一个字节不动）· **只自动跑 safe 步骤**（会改写用户文件结构的要 CLI `--run --yes`）·
  无戳的既有书稿记基线 v1、不碰数据 · 失败**不推进版本戳**。拒绝时退出码 **5**
  （2 已被「预算熔断」占用，共用会让日志里的失败原因无法分辨）。
  改数据结构 = `DATA_SCHEMA` +1 + `MIGRATIONS` 加 `Step` + 加回归，两处不同步 `status()`
  会大声拒绝而不是静默跳过。CLI：`python scripts/data_migrate.py`（`--dry-run` / `--run [--yes]`）；
  承诺与边界 → `docs/upgrade.md`。
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
- **设定集 ↔ 大纲重合度（方案 C，2026-10-19）**：stage1 归并出设定集后，拿
  `config/project.yaml` 的 `user_outline` **反查**设定集里的每个专名，算角色/地点/总命中率
  （实现 `scripts/utils/coherence.py`，报告 `data/setting/coherence.json`）。
  三态开关 `gates.setting_coherence`：`warn`（默认，打印横幅 + 挂 progress warning，
  **不改流程**）/ `block`（角色或地点一个都对不上就让 stage1 判失败）/ `off`。
  阈值 `gates.setting_coherence_min_ratio`（默认 0.15）。**为什么默认 warn**：
  大纲可能写得很抽象（不点名主角），硬拦会卡死正常流程；但**绝不能静默**——
  事故现场（设定集来自另一个宇宙、16 角色只有 2 个属于大纲）就是全程绿灯造成的。
  判据回归 `tests/unit/test_setting_coherence.py`（17 断言，含真实事故快照回放）
- **素材扫描的目录边界**：`scan_materials` 递归 `materials/raw/`，但**跳过以 `_` 或 `.`
  开头的子目录** —— `_backup/` 装的是同名覆盖前的旧版本，误扫等于把同一张卡的新旧两版
  一起喂给归并（还让素材指纹随备份抖动）。回归 `tests/unit/test_stage1_scan.py`（6 断言）
- **素材归档**：清理工作区时把整批卡搬到 `materials/_archive/{日期}_{说明}/`（**可逆**，
  附 `来源_sha256.txt`），`materials/_archive/` 已 gitignore。恢复方法写在该目录 README
- **范文（`book.style_reference`）的给法**：**一个**纯文本文件（`.md` / `.txt`，UTF-8 或 GBK，
  编码有回退链），多篇合并进同一文件（路径是单值）。stage4 每章注入原文
  ≤`book.style_reference_max_chars`（默认 **6000**，范围 1000~40000）+ 3 段 few-shot（≤900 字，
  自动避开与本章大纲/上一章相似度 >30% 的段落）；stage6 用同一份文件做量化特征比对。
  **给「场景切片」而不是整章**：2~3 个不同场景各 1500~2500 字，比一整章 8000 字更有效
  —— few-shot 只抽 3 段，场景越分散覆盖的语感越全。超长与「配了路径但文件不存在」
  都会 WARN（2026-10-19 之前是静默截断，会让人误判成「模型学不像」）
- **范文有 GUI 入口**（2026-10-19）：文风特征页签「范文正文」框 = 粘贴 + 保存/清空，
  端点是 `GET/POST /config/style_reference`。保存时**自动**把 `book.style_reference`
  指向该文件（用户不用碰 yaml）；旧版备份到 `data/state/style_reference_history/`
  （保留 20 份）；单次 ≤40000 字；路径必须落在**工作区内**（越界一律 400）。
  正文存文件而非塞 yaml —— 范文可数万字。**不设 GUI-only**：创作决策，Agent 可代写
  （与 `target_words` 同口径；`token_limit` 那类成本/安全闸门才限 GUI）
- **提示词调优**：直接改 `prompts/stageN_*.md`（模板与代码分离，无需改脚本）
