# 长篇规模能力（2026-10-20，四项一次落地）

> 原 `AGENTS.md` 的一节，2026-10-27 拆分至此（原文件 76,326 字节，超过 65,536 字节的
> 指令加载预算会被系统**静默截断**，被截掉的恰是靠后的《能力边界》《硬性约束》）。
> **纪律效力不变**：动这块代码/数据前按需读本文。

四件事共用一条判据：**宁可明说"没有"，也不给假的**。每项都有确定性用例
（`test_review_ledger.py` 48 · `test_memory_policy.py` 28 · `test_volume_arc.py` 51 ·
`test_setting_closure.py` 41）。

### 1. 七维评审账本（stage5，替代自由文本审稿）

`utils/review_ledger.py` = 唯一实现。七维与 `prompts/stage5_check.md` 的七项检查
**一一对应**（plot/timeline/space/reference/relation/persona/commonsense），
每维必须给 `score`（0-10）+ `quote`（**本章原文里逐字存在的句子**）。

- **举证对不上原文 → 该维 `score=None`（不计分，不是扣分）**；缺维不补默认分。
- 匹配前 squash 掉标点/空白（`「」` vs `“”`、半角逗号都算同一句）；
  含省略号的引用按 `…` 切段，**每段都要命中**（容纳省略，不放水）。
- 举证 <8 个有效字符不算（"他点了点头"定位不到任何一处）。
- 落盘 `data/review/NN.json` + `summary.json` + `ledger.md`；
  `check_report.md` 仍保留（给人看的自由文本，两者不互相替代）。
- 开关 `gates.review_ledger`：`warn`（默认，只告警）/ `block`（不完整即阶段失败）。
- GUI：章节页签「七维评审账本」（`GET /review/ledger`，只读，不提供重算按钮）。

**审美五问（2026-10-20，独立于七维）**：七维管"没写错"，五问管"写得好不好"——
描写质感 / 叙事手法 / 对话区分度 / 用词质量 / 情感打动力（`review_ledger.AESTHETIC`）。

- **不叫第八维、不动七维契约**：`dims` 一个字没改，审美是独立字段 `aesthetic`；
  `complete` 仍只看七维，审美单独给 `aesthetic_complete`。老账本照常读，界面显示「未评」。
- **一问一句**：同一句原文只算第一处 —— 重复给多问、或复用七维已引过的句子，一律判未举证
  （`check_chapter` 把七维的 quote 作为 `reserved` 传进去，跨块复用也拦）。
- 五问缺一即整块作废；低分进 findings 且 `kind=aesthetic`；**不参与 stage5 的 warnings**（只打印）。

### 2. 上下文记忆策略（stage4，三层）

`utils/memory_policy.py`。L1 固定层（大纲/角色卡/上一章尾）**不裁**；
L2 滚动层（全书摘要 + 最近 `rolling_window` 章）与 L3 召回层（四维：伏笔/角色/状态/关系）
按 `context.memory.*` 的字符预算裁。

- **裁掉什么必须写出来**：省略章号区间、丢掉的候选数都进注入块；
  召回为 0 时写"没有与本章相关且够近的旧事"，不是静默留空。
- 未来章节/事件**不召回**（那是剧透）。最近 3 章摘要不重复召回（由上一章尾巴覆盖）。
- 相关度 = bigram 重合数 + 3×专名命中；**专名必须同时出现在候选与本章大纲里**，
  否则设定集里每个带人名的碎片都会被无差别召回（实测踩到）。
- 纯 bigram 门槛不够：要求重合数 ≥4 **且**重合率 ≥0.2（"都能含'没有'"不算相关）。

### 3. 卷弧双层滚动规划

`utils/volume_arc.py` + `scripts/volume_plan.py`。近层（当前卷 + 后续
`rolling_volumes-1` 卷）出**逐章目标**；远层只留一句话方向。

- stage3 只铺到 `horizon`（默认 2 卷），窗口外由 stage4 **按需补齐单章大纲**
  （`stage3.generate_chapters`，与 stage3 共用一份实现）。

**章大纲六字段（2026-10-20，v0.7.2）**：核心事件 / 涉及角色 / **小目标** / **悬念钩子** /
功能 / 衔接。新增的两项是写作时的实际抓手，也是 v0.9.3 伏笔台账的唯一确定性数据源。

- **生成侧严格、存量宽容**：`check_chapter_outline(path, strict=True)` 用于 stage3
  **新生成**后校验（六字段 + 新字段值 ≥ `MIN_FIELD_CHARS`=6 字）；`outline_ok`（断点判据）
  仍用宽松模式，只查三个硬字段 —— 否则升级一次就把用户已写完的大纲全判废。
- **空值不算有字段**：旧实现只判 `f in text`，正文里出现"钩子"两字就算通过（假绿）。
  现按行首 `字段：值` 匹配，值为空即判缺。
- ⚠️ 正则里冒号后只能 `[ \t]*`，不能 `\s*`：`\s` 会吃换行，把「小目标：（空）」
  取成下一行整行（实测踩到，`test_chapter_outline_fields.py` 里钉住）。
- ⚠️ stage4 只补**文件不存在**的章大纲；**存在但字段不全绝不重写** ——
  实测那样会把已审阅的大纲整批换成占位内容。
- 卷纲判据：逐章目标**覆盖每一章** + 无占位符；不合格 → 重规划（修复），
  合格 → **绝不覆盖**（`--force` 才动）。`data/outline/volumes/vNN.md` + `plan.json`。
- `chapter.rolling_volumes: 0` = 关闭滚动，行为与改动前一致。

### 3-B. 残稿可见可采纳（v0.8.5，2026-10-20）

`utils/truncated_store.py` + `GET /truncated`、`POST /truncated/adopt|clear`。
章节页签「残稿」面板。

- 截断时我们**不落盘**（纪律正确），但残缺文本一直隔离在 `data/state/truncated/`，
  **人看不见** —— 失败之后用户只看到「失败」，不知道模型其实写了 3000 字。
- **采纳 ≠ 完成**：写进 `raw/NN.md` 的同时在 `adopted.json` 打侧车标记，
  `is_chapter_complete` 见到标记**仍判未完成**。否则采纳就是绕过审稿的后门。
- 用户补齐后点「已补齐」清标记，恢复正常判定。
- 采纳时**自动补 `## 第N章` 标题**：残稿是正文没有标题，不补的话补齐后仍过不了
  标题判据（实测踩到，用户会以为"点坏了"）。
- 太短（< 300 字）不给采纳，并说明"多半刚开始就断了，重写更省"。
- 侧车标记不写进正文：正文加注释会污染导出与字数统计。

### 3-C. 伏笔台账（v0.9.3 前半，2026-10-20）

`utils/foreshadow.py` + `GET /foreshadow`、`POST /foreshadow/status`。章节页签「伏笔台账」。

- **数据来源只有两个确定性入口**（不额外烧 token）：章大纲的「悬念钩子」（v0.7.2 起强制）
  + 后续章的「核心事件/小目标/功能」文本。
- 推进判据：bigram **交集 ≥2 且覆盖率 ≥0.15**。只用交集数会"短钩子太松、长钩子太严"；
  只用覆盖率短句随便撞一个就 20%。两者一起才稳。
- **回收不自动判定**：文本相似不等于"解决了"。`closed`/`abandoned` 只能人点，
  且**必须写理由**（唯一能防止台账被随手清空的东西），人工标记压过自动判定。
- 超期告警：`open|advanced` 且闲置 > `gates.foreshadow_stale`（默认 30 章）。
- 没有章大纲 → 返回空 + 明确说明"无从抽取"，**不倒假数据**。

### 4. 设定库闭环

- **增量合并** `obsidian_integrate.merge_setting_from_vault`（CLI `merge`）：
  只做加法 —— 新增追加、空字段补齐、**已有非空值不动**、`locked` 完全不碰、
  `plot_fragments`/`timeline` **保留**。字段分歧写 `data/setting/vault_conflicts.json`
  等人裁决，不自动选一边。（`scan` 仍是整体替换语义，勿混用。）
- **大纲注入** `utils/setting_brief.py`：stage2 现在拿到的是**有预算的设定集摘要**
  （角色/地点势力/伏笔/时间线 + vault 未归并词条），不再只是一个文件路径。
  超预算截断会写明"另有 N 行未内联"。
- **GUI 拍板**：设定页签「素材裁决」→ `GET /setting/conflicts` + `POST /setting/status`
  三态（采纳/否决/待定）；否决**必须填原因**（写墓碑，按名字生效）。

### 3-D. step 级 checkpoint：崩溃窗口恢复（stage4，2026-10-21）

stage4 单章顺序是「LLM 写盘 → 归一化 → 质检 → 摘要 → 出场 → 记账 → 标完成」。
进程在「写盘之后、标完成之前」崩掉（kill / 断电 / 熔断恰好落中间）时，
盘上留着**一份合格正文**而 progress 没标 done。修复前断点续跑把它当"没写过"→
**重调 LLM 重写一遍**（一章几千 token，100 章的书崩一次白烧一遍，新一版往往还更差）。

- **判据**：`raw/NN.md` 合格但没标 done 的章 → 走 `_finalize` 幂等补账
  （摘要/出场/标完成），**零 LLM 调用**；半成品（字数不足）**不许**捞起，必须重写。
- `_finalize` 是**唯一**的"写盘后收尾"实现（主循环成功路径与崩溃恢复路径共用）——
  两份同步逻辑必然漂移（这是把内联尾巴抽出来的唯一理由，不是重构洁癖）。
- 关键：`append_chapter_summary` 是**同章覆盖**语义 → 补账不会污染 `global_summary`；
  会污染的是"重调 LLM 重写整章"（再走一次全流程 = 摘要重复压一行），
  那条路已被"正文合格就不重写"堵死。
- 自检 `tests/unit/test_stage4_crash_recovery.py`（12 断言）：幸存章不重写、
  补账后 done + 摘要进 rolling.md、半成品仍重写、正常路径不受影响。

### 交接稿 / 指南针 / 写作预设 / 首跑向导（2026-10-21，roadmap 10.1 / 7.3 / 10.3 / v0.20）

**交接稿**（`utils/handoff.py`，roadmap 10.1）：把「写到哪了 + 接下来写什么」
拼成一份可粘给**任何模型**的 markdown —— 换模型/换会话不再靠重跑。六段拼装
（身份/写作要求/设定摘要/未回收伏笔/最近章节/下一章大纲全文内联），零 token。
- 缺料**明说不编造**：下一章大纲没生成就写明「尚未生成 + 怎么补」，绝不拿空段糊过去。
- 绝对路径不出稿：所有输入先过 `strip_protocol_markers`，输出再过绝对路径扫描
  —— 交接稿要去别人家，不能带门牌号。
- 入口：`nfctl handoff [--chapter N] [--print]` · `POST /handoff/export` ·
  章节页签「交接稿」面板（复制全文 / 打开文件）。自检 30 断言。

**指南针**（`utils/compass.py`，roadmap 7.3）：`global.md` 是第 1 天定的全书大纲，
写到第 100 章时早改得面目全非 —— 指南针是「全书往哪去」的单一锚点：
终局方向一句话 + 活跃长线（≤5 条）+ 规模估计。
- 活跃长线 **>5 条拒绝写入**（长线太多等于没有主线）；规模与实际章数偏离 >30% 告警不阻断。
- 终局方向变更自动留 `data/outline/history/compass_v*.json`（切回有据）。
- 没设时 stage3 明说「未设终局方向」——**静默省略会让长篇一路写偏而无人知**。
- 入口：大纲页签「指南针」面板 · `GET/POST /outline/compass`。自检 21 断言。

**写作预设**（`utils/writing_presets.py`，roadmap 10.3）：多套写作要求存档与一键切换。
- **只收写作要求**（style/style_notes/target_words/form/language/genre）；
  书名/章数/作者是**作品身份**，不进预设 —— 身份字段混进来，切换会把 A 书改成 B 书。
- 切换 = 校验 → 留 `data/presets/writing/history.jsonl` → `set_book_fields` 定向写回
  （含备份 + 回读校验）。未知键一律拒绝并列出可选键。
- ⚠️ `set_book_fields` 按**脚本位置**定位 project.yaml 与备份目录（模块级常量）——
  root ≠ 仓库根时会写错书、备份落进真实仓库，故用完还原（同 foreshadow 的坑）。
- 入口：设置 → 风格与提示词 → 「写作预设」面板 · `GET/POST /config/writing_presets{,/save,/apply}`。
  自检 25 断言。

**首跑向导**（roadmap v0.20）：新建项目向导加「选出厂预设」一步 ——
选中即铺配套素材 + 配置，**否则首跑是空跑**。
- `GET /factory/presets`（只读清单，Agent 可调）· `POST /factory/preset/apply`
  （**破坏性**：快照 → 归档当前项目 → 铺素材 → 写配置）。
- 铺设列入 `FORBIDDEN_IN_AGENT_MODE` + 端点自身 `_gui_only` 双因子（与 `/project/create`
  完全同族）。Agent 可盘点、不能动手。自检 17 断言（含伪造来源头的红队路径）。

### 弹窗写法（暮雨 2026-10-19 点名"不会抄自己"）

应用里已有范式，**不要手搓**：`drawer-mask`（居中）→ `dialog` → `card-head` 标题 →
可滚动正文 → `dialog-actions`。参照 `DisclaimerDialog.vue`。
`v-if` 动态插入时 `autofocus` **不生效**，必须 `watch + nextTick + focus()`；
默认焦点放在**最保守的选项**上（"我不同意"）。
