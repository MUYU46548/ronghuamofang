# 设定引用过期（M10）

> 原 `AGENTS.md` 之外的单篇说明。**动 `utils/setting_stale.py` 或改这里的行为前先读完**。

## 要治的病

设定集条目写得再细，也**没有任何东西记录它依据哪一章**。作者改完第 3 章，引用第 3 章
的设定条目光看界面完全看不出“可能已经不对了”，只能凭记忆重读全书。

问卷 M10 的答案（`mofang.setting.stale` = A）：条目记 `sources`（章号 + 版本），
改章即标待核，逐条核对后恢复。

## 数据形状

`sources` 写在**条目自身**（`data/setting/setting.json` 的 characters / world 子列表 /
timeline / plot_fragments 条目里），不是另一个账本文件：

```json
"characters": [
  {"name": "沈砚", "role": "巡道工",
   "sources": [{"chapter": 3, "version": "ab12cd34ef56", "layer": "refined",
                "linked_at": "2026-11-04T12:00:00", "confirmed_at": null}]}
]
```

- `version` = 该章**当前稿**正文的 sha256 前 12 位；`layer` 记是哪一层。
- 当前稿口径与编辑器 / 病灶扫描 / 重写工作室**同一条链**：`refined > checked > raw`
  取第一份存在的。换层（raw → refined）也算变 —— 正文的载体换了，内容值得重看。
- 写回走 `utils.file_io.write_text`（原子替换）+ **先备份**到
  `data/setting/history/setting_vN.json`（与 `setting_refine` / `setting_versions.restore`
  同一命名，别另起一套）。

## 判据为什么在**读**的时候算

`GET /setting/stale` 现算，不落状态文件：

- 改了章，界面刷一下就红 —— 不存在“标记没跟上”的二次故障；
- 状态文件一旦存在，就会出现“章改了但标记没刷”“回滚了但标记还是红”这类
  本仓最忌讳的静默失真；
- 代价是每次读都要扫一遍章节正文（零 token、纯字符串，几十章量级可忽略）。

**`checked=false` = 没查**（设定集缺失/损坏），不是“没有问题”—— 与冲突清单同一条红线。

## `link` 为什么只增不改

补登记（`POST /setting/stale/link`）**只新增没登记过的“条目 × 章节”**，
已登记的那一条哪怕章已经改过也**不刷新版本**。

否则“点一下扫描”就把待核全洗白了 —— 机制等于摆设。恢复只能由作者逐条确认。

## 名字匹配为什么长词优先

条目名互相包含是常态（“沈砚”与“沈砚之”）。实现按长度降序匹配，命中后把该位置
挖空（替换成 `\x00`）再匹配短词 —— 否则“沈砚之”出场的地方会被同时记成“沈砚”的引用，
凭空多一条待核（误报方向会训练作者忽略清单）。

- 短于 2 字的名字**不参与匹配**（单字名在中文正文里必然误报），但也不静默丢弃：
  报表单列 `counts.unlocatable`。
- 条目支持 `aliases` / `alias` / `别名`（字符串或数组）。

## 状态词（别混）

| state | 含义 | 界面 |
|---|---|---|
| `stale` | 登记过，但章改过 / 换层 / 章没了 | 待核，逐条给“已核对”/“移除来源” |
| `new` | 章里出现了但还没登记 | 新引用，提示去扫描 |
| `ok` | 登记过且对得上 | 只在计数里 |
| `absent` | 从未在任何章里出现，也没登记 | 只在计数里（不冒充“检查过”） |
| `unlocatable` | 名字太短，没法定位 | 计数 + 提示 |

## 权限（与审批同族，不要放宽）

- `GET /setting/stale`、`POST /setting/stale/link`：**Agent 可用**（一个读、一个机械登记）。
- `POST /setting/stale/review`（confirm / drop）：**只归作者**。
  端点内部 `_gui_only`（双因子）+ `agent_guard.FORBIDDEN_IN_AGENT_MODE` 双闸；
  CLI 的 `confirm` / `drop` 在 `agent_mode=true` 下由 `cli_approval_gate` 无条件拒绝。
  为什么：Agent 一条 POST 就能把“引用可能已不成立”洗成“已核对”，与代签审批同族。

## 接线四处（漏一处就到不了界面）

1. `scripts/nf_api_domains/setting_state.py`：`handle_setting_stale*` + `ROUTES`
2. `scripts/nf_api.py`：GET/POST 各一行薄转发
3. `scripts/utils/agent_guard.py`：`/setting/stale/review` 进禁用名单
4. `scripts/nf_mcp.py`：`nf_get_setting_stale`（读侧；**技能卡必须提到它**，
   `test_phase_skills` 的 MCP→skill 判据会红）

GUI：`console/src/SettingStalePanel.vue`，挂在“设定”页签 `SettingClosurePanel` 之前。

## 刻意没做的事

- **不在 stage4 / stage6 结束时自动 link**：那会在长跑里反复写正典（setting.json），
  且“登记”本身是作者理解机制的动作。第一次点一次“扫描章节并记来源”即可。
- **不把 stale 写进导出拦截**：成品过期有 `_stale_info` 那一套（源章 mtime vs 成品），
  两件事不要混 —— 设定待核是“内容可能不成立”，不是“文件比源旧”。
- **不做段级精确定位**：目前只到章级。段身份在章内有效（见产品定义 §3.6），
  精确到段就是假精确。

## 已知边界

- **stage1 重归并 / 设定集恢复（`/setting/restore`）会重写 setting.json** →
  条目上的 `sources` 随之消失（那是另一个版本的条目）。回到面板点一次
  “扫描章节并记来源”即可重建；界面会明说“还没有条目登记过来源”，不会假装一切正常。
- 名字匹配是字面子串：偶尔撞上正文里的普通词会多记一条引用（报表给出命中章与原因，
  作者可以“移除来源”）。这是**误报方向的有意选择** —— 漏报会让待核清单安静地少一条，
  误报只是多点一次。

## 判据

`tests/unit/test_setting_stale.py`（12 组，含反例）：

- `checked=false` 不冒充没问题；`link` 无设定集时明确失败；
- 长词优先（沈砚之不给沈砚记引用）；短名计入 unlocatable；
- 改章 → 只影响引用它的条目；`link` 再跑不重复、**不洗白待核**；
- confirm 更新版本 + `confirmed_at` + 备份；章没了 → missing，confirm 拒绝，drop 可去；
- 换层也算变；新引用记 `new` 不冒充 stale；`status` 只读（字节不变）；
- 路由 / 禁用名单 / MCP 接线；参数校验；world 子分组可定位。

真机：`tests/e2e/e2e_setting_stale.py`（mock 喂两条待核 + 一条新引用，
验预演→确认→核对，截图在 `Temp/gui_verify/ux/`）。
