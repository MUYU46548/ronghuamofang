# 绒花墨坊 · ①执行单 v2.2 修复验收报告

**日期**：2026-09-29
**依据**：`shared/quickfix-plan-20260928.md`（执行单 v2.2）+ `shared/report-20260929/汇总报告-20260929.md`
**状态**：7/9 件已落地并通过自检；2 件因**缺实物**无法执行（见 §3）
**提交**：已分批提交 5 个 commit（044e3a7 / b6df4ef / c1a35b4 / 9769620 / 见 §6）
**打包**：已产出 `console/dist/ronghuamofang-console-setup-0.3.2.exe`（`--publish never`，未上传 GitHub）

---

## 1. 结论

六件代码件 + 随行 5 件中，**件2/3/4/5/6/7 + 随行1/2/3/5 已全部落地**，每件都配了可复跑的
自检（新增 5 个测试文件，合计 63 条断言），并跑通全量回归。**件1 与 ⑤前置步0 因文件在本机
不存在，无法执行** —— 需要暮雨提供实物。

---

## 2. 逐件验收

| 件 | 改法 | 自检（可复跑） | 结果 |
|---|---|---|---|
| 件2 解析容错 | `<think>` 剥离；断裂 JSON 补括号兜底；`LAST_ERROR` 带原文偏移；`_salvage_answer` 捞回「真能 json.loads 的 JSON」 | `tests/unit/test_json_salvage.py` | **24/24** |
| 件3 outliner 锚点下限 | 提示词写入硬约束；新增 `check_outline_anchors`（THIN>1 打回）；`check_global_outline` 保持纯净 | `tests/unit/test_outline_anchor_gate.py` | **9/9** |
| 件4 质量闸门化 | 全流程收尾强制跑 `quality_checklist`，非零退出 → exit 1；`gates.quality_gate` 默认 true | `tests/unit/test_quality_gate.py`（真跑 `orchestrator.run()`） | **6/6** |
| 件5 MCP 两把钥匙 | 新增 `nf_get_remedy` + `nf_restore_snapshot`（预览/确认两段式 + `pre_restore` 折返守卫）；MCP 工具 20→22 | `tests/unit/test_mcp_remedy_snapshot.py`（A/B/C） | **17/17** |
| 件6 分层指引 | 8765 不可达 → 带 8766/8765 层号的干净报错；网关 5xx 视同服务不可达；修正 docstring 传输虚标 | 同上（B 节）+ `README.md` FAQ | **17/17 内的 B 节全绿** |
| 件7 stage5 输入构造 | 批次列表 `f.name` → `f.resolve()`；设定集行补 `- ` 前缀 | `tests/unit/test_stage5_inputs.py`（A 修复 + B/C **反证**） | **7/7** |
| 随行1 | orchestrator 循环内 `import time` 上移模块头 | 全量回归 | 通过 |
| 随行2 | RATES 补 `kimi-k2.6`；fallback 摘除 `glm-5`（10-09 下线） | `test_config_and_models` 等 | 通过 |
| 随行3 | `segment_requests` 指令**移尾**，救回 N 子请求的前缀缓存 | 全量回归 | 通过 |
| 随行5 | e2e 视觉验收列入 v0.3.2 发版前必跑（`CHANGELOG.md`） | — | 已记录 |

### 全量回归（实测）

- `scripts/quality_gate.py`：**BLOCK 0，通过**（WARN 40 为历史存量）
- `scripts/nfctl.py test`（tests/unit + tests/http）：**41 文件 / 41 通过 / 0 失败**
- e2e 离线：`test_stop_and_mingjian` 38 · `test_gui_api_contract` 40 · `test_all_tasks` 34 ·
  `test_p0_all` 21 · `test_p0_fixes` 14 · `thinking_compat --offline` 45 —— **全绿**

### 两处「反证」（证明修复不是「本来就好」）

1. `test_stage5_inputs.py` B/C 节：复刻修复前的**裸文件名**与**非列表行**形态，
   断言二者**必须**分别复现「章节判 missing」与「设定集不被解析为输入」。
2. `test_quality_gate.py`：用**同一份红产物**跑 `quality_gate=true`（exit 1）与 `false`（exit 0），
   证明的是「闸门生效」，不是「碰巧退出码是 1」。

---

## 3. ⛔ 无法执行的两件（缺实物）

| 件 | 证据 | 需要什么 |
|---|---|---|
| 件1 `agent.json` 缺字补键 | 全仓 + `E:/CODE` 下 `grep`/`find` **零命中**；`git log --all` 全历史亦无此文件名 | 文件本体或明确路径（疑似在哨兵侧，未同步到本机） |
| ⑤前置步0 stdio 垫片收编 | `nf_mcp_stdio_bridge.py` 在仓库、`shared/`、`C:\Users\muyu\Downloads\shared\` 下**均不存在**（该目录只有两份报告 md） | 垫片文件本体 |

---

## 4. 顺带修掉的既有缺陷（非本次引入）

`tests/e2e/test_gui_api_contract.py` 的「do_POST 链未被打断」判据**长期误报**：原实现扫描
do_POST 的**全部顶层语句**，把 Try **之前**的「Agent 模式安全守卫」(`if request_source != "gui":`)
算成「第二条裸 if」。按该段自述意图（「Try 体的顶层语句」）收窄到 Try 体 → 40/40。
该文件不在 `nfctl test` 范围内，所以此红一直没被发现。

---

## 5. 定价入口（回答暮雨 2026-09-29 的问）

**入口是有的，2026-09-23 就上线了**（commit `74230f8` 后端端点 + `3272e3b` 前端面板）：

| 入口 | 位置 | 能力 |
|---|---|---|
| GUI 定价编辑器 | 控制台 →「成本」页签 → 右上「定价」按钮 | 表格式增删改 `in` / `out` / `cache_read`；列「来源」区分 默认/自定义；保存即时生效 |
| 后端 | `GET/POST /costs/rates` → `data/state/cost_rates.json` | 自定义价**覆盖**源码 `RATES`（`get_merged_rates()` 合并视图） |
| CLI | `python scripts/price_wizard.py` | 交互式「新增/修改/删除模型」，直接改 `cost_tracker.py` 源码 |

**真实缺口**：只有**逐行编辑**，没有**批量粘贴导入**（例如把 TokenHub 价目表整表贴进来
一次落库）。如果"早就提过"的是这个，需要新增一个粘贴框（解析 `模型名 / in / out / cache`
四列或 JSON，预览后写入 `cost_rates.json`）。

**当前 `kimi-k2.6` 的状态**：源码 `RATES` 里放的是**占位价**（= default 回退价同值，
只为不再刷 "未知模型回退" WARN）。**要落到真实价，两条路**：
① 你直接在上面那个「定价」面板填（写 `data/state/cost_rates.json`，用户数据、不进 git）；
② 把 TokenHub 上的三个数字给我，我改进源码 `RATES`（对所有环境生效）。

---

## 6. 提交记录（分批）

| # | commit | 范围 |
|---|---|---|
| 1 | `044e3a7` | 解析层容错 + 大纲锚点闸门（件2/件3） |
| 2 | `b6df4ef` | 质量闸门化 + 摘 `glm-5` + 补 `kimi-k2.6`（件4/随行1/随行2） |
| 3 | `c1a35b4` | MCP 两把钥匙 + 服务分层指引（件5/件6）+ 顺带修 e2e 误报 |
| 4 | `9769620` | stage5 输入构造修复（件7）+ CHANGELOG |
| 5 | 见下 | `nfctl.py` 成本显示修复（**2026-09-28 既有改动**，非本轮） |
| 6 | 见下 | 打包 v0.3.2：`SEED_VERSION` 3→4 + version 0.3.1→0.3.2（**含 console/package.json 里 2026-09-28 的 dev 脚本改动**，同文件无法拆分 hunk） |

**未提交（待你定）**：
- `materials/vault_links.md` —— stage1 生成的**数据产物**（168 行 diff），提交它等于把生成物入库，建议不提交；
- `config/project.yaml.bak` —— 备份文件，建议删除或加进 `.gitignore`。

---

## 7. 打包记录（v0.3.2）

**顺序（已按代码实证，与早前笔记相反 —— 见下）**：`SEED_VERSION` 3→4 → `npm run build`
→ `npm run dist`。

早前笔记写的是「先 build+dist 再 bump」，**我核对代码后判定该顺序是错的**：
`SEED_VERSION` 定义在 `console/main/index.js`，会被打进 `app.asar`；而 payload 是
electron-builder 的 `extraResources` 在 **dist 时从活的 `../scripts`、`../prompts` 现拷**。
若先 dist 再 bump，**装出来的包版本号仍是旧的 → 用户永远不会刷新 workspace**。
bump 前置则两个条件同时满足，且已用产物核验（见下）。

**产物核验（全部实测）**：

- `console/dist/ronghuamofang-console-setup-0.3.2.exe`（97.5 MB）+ `.blockmap`；`latest.yml` → `version: 0.3.2`
- payload 含本轮全部改动：`strip_think`(2 文件) · `check_outline_anchors`(1) ·
  `_service_unreachable_text`(1) · `quality_gate`(3) · `锚点数下限`(2)
- `app.asar` 内 `SEED_VERSION = 4` ✅

**未做 / 需你决定**：
- **未上传 GitHub Release**（`--publish never`）。自动更新还需要 `latest.yml` + setup.exe
  作为 release 资产上传，且需要 `GH_TOKEN`（`gh` 当前未登录）。
- **e2e 视觉验收（`tests/e2e/e2e_ux_verify.py`，64 断言）未跑** —— 本机 **未安装
  playwright**（`node_modules/playwright` 不存在），且它还需要 8091/8798/8799 三个端口与
  已构建的 renderer。按随行件5 它是"v0.3.2 发版前必跑"，**正式发版（上传）前请补跑**，
  或告诉我装 playwright。

---

## 8. 本轮抓到的额外缺陷（原单未列）

**`config/system.local.yaml` 覆盖了 fallback 修复**：该本地文件（不进 git）的
`fallback` 里**仍有已下线的 `glm-5`**，而深合并对**列表是整体替换**——只改
`config/system.yaml` 等于没改。已在本地文件里一并摘除（该文件不在 git，故无对应 commit）。

**同类风险提示**：`available_models` / `disable_thinking_models` / `fallback` 三个列表
都在这份本地文件里被整体覆盖，改 `system.yaml` 时**必须同时检查它**。

