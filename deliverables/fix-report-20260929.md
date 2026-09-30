# 绒花墨坊 · ①执行单 v2.2 修复验收报告

**日期**：2026-09-29
**依据**：`shared/quickfix-plan-20260928.md`（执行单 v2.2）+ `shared/report-20260929/汇总报告-20260929.md`
**状态**：7/9 件已落地并通过自检；2 件因**缺实物**无法执行（见 §3）
**提交**：已分批提交 5 个 commit（044e3a7 / b6df4ef / c1a35b4 / 9769620 / 见 §6）
**打包**：已产出 `console/dist/ronghuamofang-console-setup-0.3.2.exe`（`--publish never`，未上传 GitHub）
**追加批次（2026-09-30）**：定价批量导入上线（§13）· `--root` 路径纪律存量**清完并加护栏**（§12）

---

## 0. 追加批次速览（2026-09-30）

| 批次 | 内容 | 验证 |
|---|---|---|
| 第七批 | **发版保障**：`nfctl release-check` 一条命令跑完五项检查（含**产物核验**，首跑即抓到"改了代码没重打包"）；`nf_mcp_handshake_check.py` 真机握手自检；`docs/mcp-connect.md` 接入指南；`requirements-dev.txt` 补登 playwright/mcp | `release-check` **exit 0 全绿**；握手自检 7/7 |
| 第六批 | **MCP stdio 垫片**补位（执行单 ⑤前置步0/1）——标准 MCP 客户端终于接得上 8766 | `test_mcp_stdio_bridge` 15/15（含 2 处反向验证打红）；**真机握手 7/7**（官方 SDK） |
| 第五批 | `--root` 路径纪律：12 处相对数据路径改经 ROOT + `_set_root()` 派生常量 + **可执行护栏** | `test_root_path_discipline` 9/9（含两处反向验证打红）；`nfctl test` 44/44；e2e 74/0 |
| 第四批 | 定价批量导入（粘贴 → 预览 → 确认），认表格 / JSON / RATES 字面量 | `test_rates_import` 53/53；e2e 新增 10 条断言 |

**§3 已结案**：原「无法执行的两件（缺实物）」经查证**均非本仓库产物**——
件1 `agent.json` 判定为跨 agent 串味（全库零引用）；⑤前置步0 的垫片需求描述完整，
已**按需求重写**（详见 §3 与 §14）。**GitHub Release 上传**按暮雨指示不急，
本就标注为早期开发阶段、不推荐现在就用，随版本积累再发。

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

## 3. ⛔ 无法执行的两件（缺实物）—— **2026-09-30 已结案：来源查明**

两件都**不是本仓库的东西**，出处全在 `shared/report-20260929/开工执行单-v2.2.md`，
且都是**「哨兵」这个角色**（负责实跑取证的另一路 agent）提出的，产物留在**它自己的沙箱**里、
从未落到本机磁盘。执行单自己就写了原因：「**按今天产出蒸发教训，落盘必须落库**」。
（本仓 + `E:/CODE` + git 全历史 + Hermes 技能库 + `Downloads/shared/` 全部零命中；
`Downloads` 那个 zip 里只有两份 md，没有垫片也没有 agent.json。）

| 件 | 执行单原文 | 结论 |
|---|---|---|
| **件1 `agent.json`** | 「**哨兵 fetch 差异 + 我方工作树**（非梅林 §7 范围，采信不阻塞）」；改法「按模板补齐缺失键」 | **判定为串味**：本仓库既无 consumer 也无 producer（全库 grep 零引用）。"我方工作树"指哨兵那边的沙箱。**关闭**。 |
| **⑤前置步0 垫片** | 「`shared/nf_mcp_stdio_bridge.py` → `scripts/`，保留哨兵文件头…按今天产出蒸发教训，落盘必须落库」 | **不需要等实物**：需求描述完整（含 ⑤前置步1 的验收标准），**已按需求重写**，见 §14。 |

> 教训记一条：**跨 agent 协作时，"我方工作树"这种指代没有锚点就不可复现**。
> 以后再引用别的 agent 产出，必须带**绝对路径 + 内容摘要**，否则就是"等一件不存在的快递"。

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

**第三批（同日）：** `21d908d` 忽略 `*.bak` · `207122c` CORS 放行 `X-Mofang-Source` + e2e 可复现 ·
`<见 git log>` 前端端口收敛（`console/src/apiBase.js`）+ 打包 v0.3.2（SEED_VERSION 5）。

**未提交（待你定）**：仅 `materials/vault_links.md`（stage1 生成的**数据产物**，168 行 diff）
——按你的指示**不提交**。
`config/project.yaml.bak` 已由新增的 `*.bak` 规则忽略（不再出现在 `git status`）；
它当前没有任何代码会生成（手改残留），是否删除由你定。


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

---

## 9. 第三批：把「发版前必跑」的视觉验收从跑不起来修到可复现

**起点**：`tests/e2e/e2e_ux_verify.py`（64 断言）本机**根本跑不起来** —— 首条断言即崩，
0 通过。逐层挖出 4 个独立缺陷：

| # | 缺陷 | 性质 | 修法 | 证据 |
|---|---|---|---|---|
| 1 | `nf_api._cors` 的 `Access-Control-Allow-Headers` 只有 `Content-Type`，而前端 api() **每次都发 `X-Mofang-Source`** → 预检必失败、所有请求被拦 | **产品** | 补该头；Allow-Origin 仍只放行本机来源，安全不受影响 | 修前页面只有骨架；修后 `/state` 等全部 200 |
| 2 | 假后端只存在于 `Temp/mock_nf_api_state.py`，`Temp/` 被 .gitignore 忽略，而 AGENTS.md 早把路径写成 `tests/e2e/` | **验收基建** | 迁入 `tests/e2e/mock_nf_api_state.py` 并补齐 CORS 头 + `/costs/rates`、`/sandbox/queue` | 干净克隆现在可复现 |
| 3 | 前端靠 Electron preload 提供 `window.mofangAPI`，纯浏览器下未定义 → App.vue 4 处 `readPreview(...)` 抛 TypeError | **验收基建** | 加 preload 垫片（按 `console/preload/index.js` 同名同形）；`readPreview` 是 Electron 独有 IPC，垫片返回 `ok:false` 并**显式标注为已知覆盖缺口** | 修前 5 次 TypeError，修后 0 |
| 4 | **6 个面板各自硬编码 `http://127.0.0.1:8765`**（Style/Scraps/Review/Proofread/ParagraphRefine/ChapterBlueprint），绕过 `__NF_API_BASE__` | **产品** | 收敛到新模块 `console/src/apiBase.js`，7 处统一 import（判据从 7 份变 1 份） | 修前 console 成片 `ERR_CONNECTION_REFUSED`（且错误不带 URL）；修后「无」 |
| 5 | 「版本号取自 console/package.json（非 dev）」断言**硬编码 `v0.1.0`** —— 版本一升必红，且能误匹 mock 的 `0.1.0-mock` | **断言本身写错** | 改为实时读 `console/package.json` 的 version 且排除 mock | 修后通过 |

**结果**：`0 通过（崩）` → 60/4 → 62/2 → **64 通过 / 0 失败（exit 0）**。

**那 4 条怎么收的（第四批）**：给「真后端」段补了**夹具项目** `tests/e2e/fixture_project/`
（状态固定「1-4 done 且 approved；5-7 pending」→ 下一步必为阶段 5），
真后端按 `--root tests/e2e/fixture_project` 起。**没有改期望值去迁就本机数据**。

做夹具的过程又逼出 3 个真缺陷（都属「静默给错东西」）：

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 6 | **`/about` 的版本落在 ROOT 上**（`ROOT/console/package.json`） | `--root` 指向别的项目时那边没有该文件 → 关于弹窗显示 **`vdev`** | 改为「ROOT 优先 → **代码所在仓库**兜底」（版本属于应用，不属于当前书档） |
| 7 | **`--root` 未 `resolve()`**（`main()` 与 `NF_ROOT` 两处） | 相对 `--root` 让 ROOT 随 CWD 漂移；`/about` 把相对路径当「项目根」展示（实测 `tests\e2e\fixture_project`） | 两处补 `resolve()` |
| 8 | **`ProgressManager._load()` 静默降级** | progress.json 解析失败 → 界面显示「全部阶段未开始」，**下一次 save() 用默认值整体覆盖用户状态**（真丢数据） | 大声告警（带路径+原因）+ 先把原件另存 `progress.json.corrupt-<ts>` 再降级。自检 `tests/unit/test_progress_corrupt.py`（11 断言） |

**还踩到一个 gitignore 陷阱**：`.gitignore` 的 `data/` 是**无前缀**目录规则，会连坐
`tests/e2e/fixture_project/data/` → 夹具的状态文件**没进版本控制**（提交后核对文件清单才发现）。
已加两条例外放行。**再次印证「改了 .gitignore ≠ 入了库」，必须 `git status` 见到**。

---

## 11. （历史清单）`--root` 路径纪律待批 —— **已由 §12 清完**

「API 层一律经 `ROOT`、禁止相对路径」这条纪律**尚未清完**。本次只修了 `/state`
（夹具与真实数据的关键分歧点），其余登记在此，**不盲扫**（部分是把路径当字符串传给别的模块、
或用于展示/比较，机械替换会改语义）：

**`scripts/nf_api.py`**：`GLOBAL`(L210) · `HISTORY_DIR`(L211) · `AUDIT_LOG_PATH`(L214) ·
`STAGE_OUTPUTS`(L869-874) · `ProgressManager("data/state/progress.json")` ×4（L897/982/993/1113）·
`review_report.json` ×2（L1079/1097）· `proofread_report.json`(L1153) ·
`write_task("data/state/tasks", …)`(L1371) · `ov.review(…, "data/setting/setting.json")`(L1380) ·
`kb_index.build_index(…, "data/state/kb_index.pkl")`(L1675)

**域模块**：`materials.py:79`（scraps_index）· `outline.py:96-99`（setting/global）·
`refine.py:129/191/231`（章节目录链）

**建议**：单独一批做，且**按「是 Path 还是字符串」分类**处理 ——
字符串常量（如 `STAGE_OUTPUTS` 的键值、展示用路径）改 Path 可能影响比较与显示；
判定标准是「`--root` 指向别的项目时，这个端点的返回是否还是本项目的数据」，
可以照 `/state` 的办法写一条**隔离性自检**（同端口起两个 root，比对返回）来逐条验收。


---

## 10. 备份文件的定位（回答「不是应该和用户数据一起走统一导入导出吗」）

**结论：应该，但那套「统一导入导出」目前并不存在。** 现状：

- `.gitignore` 里有一条**预告性质**的注释「用户私人数据（不进 Git，**后续由"导入/导出"功能自行管理**）」——
  「后续」至今没落地：全库没有任何「用户数据导出/导入」端点或脚本。
- 现有的**项目归档/恢复**（`scripts/switch_book.py --archive/--restore`）只搬 `data/` 下的
  8 项（progress/setting/outline/chapters/summaries/merged/state/materials_manifest），
  归档到 `data/books/{书名}/`。**`config/`（含 config/history 备份、书名/风格/模板路径）
  与 `materials/`（素材卡、碎片）都不在归档范围内** —— 所以备份确实没跟着用户数据走。
- 另一条相关线是 `NOVELFORGE_DATA_DIR`（`orchestrator.get_data_root()`，13 行）—— 只是把
  `data/` 挪到项目外的**环境变量钩子**，且只有 orchestrator 在读，未接到备份/归档上。
- 好消息：备份**目录**其实已被忽略（`.gitignore` 的 `history/` 规则覆盖
  `config/history`、`data/chapters/history`、`data/outline/history`）；本次补的 `*.bak`
  只针对散落的单文件备份。`config/project.yaml.bak` 目前**没有任何代码会生成它**
  （全库 grep 无写入点），是手改残留。

**建议（需你点头，涉及搬用户数据）**：把 `config/`（至少 `history/` 与 `project.yaml`）与
`materials/` 纳入归档范围，或新建真正的「导出/导入」包（zip 用户数据 + 版本号 + 校验）。
这两条都会改变既有行为，我不擅自动手。


---

## 12. `--root` 路径纪律：存量已清 + 可执行护栏（2026-09-30）

§11 列的是待批清单，本次**全部清完**，并把纪律变成**可执行护栏**，不再依赖"下次记得"。

### 12.1 修了什么

| 位置 | 原形态 | 现形态 | 原先的后果 |
|---|---|---|---|
| `GLOBAL` / `HISTORY_DIR` | 模块级 `"data/outline/global.md"` | 由 `_set_root()` 派生 | import 时固化 → 改了 ROOT 它们仍指旧项目 |
| `main()` 的 `--root` 处理 | `global ROOT; ROOT = …` | `_set_root(args.root)` | 同上（派生常量不刷新） |
| `ProgressManager(...)` ×4 | 裸相对 | `ROOT / "data/state/progress.json"` | `/stage/skip`、`/approve`、`/reject` 读错项目的进度 |
| `chapter_review.run_review(report_path=…)` | 裸相对 | `ROOT / …` | 审稿报告写进 CWD 那个项目 |
| `batch_refine.run_batch_refine(report_path=…)` | 裸相对 | `ROOT / …` | 同上 |
| `act_proofread_run(report_path=默认参数)` | **默认参数**里的相对路径 | 默认 `None`，函数内解析 | 默认参数在 `def` 时求值 → `--root` 对它**永远无效** |
| `/proofread/run` 的 `body.get("report") or "data/…"` | 裸相对 | `ROOT / …` | 同上 |
| `client.write_task("data/state/tasks", …)` | 裸相对 | `ROOT / …` | 大纲迭代任务写进 CWD |
| `ov.review(…, "data/setting/setting.json")` | 裸相对 | `ROOT / …` | 大纲评审读错项目的设定集 |
| `kb_index.build_index(…, "data/state/kb_index.pkl")` | 裸相对 | `ROOT / …` | 知识库索引落错地方 |
| `cr.add_comment_to_finding("data/outline/review_report.json", …)` | 裸相对 | `ROOT / …` | 审稿评论写进 CWD |
| `RunDB("logs/runs.db")` ×2 | 裸相对 | `ROOT / …` | 迭代记账写进 CWD 的 runs.db |
| `nf_api_domains/outline.py`（迭代趋势端点） | `"data/setting/setting.json"` / `"data/outline/global.md"` | `api.ROOT / …` | 体检读另一个项目的大纲与设定集 |

### 12.2 刻意**不改**的（附理由）

- `AUDIT_LOG_PATH`、`SCRAPS_DIR_DEFAULT`、`RAW_DIR`、`STAGE_ARTIFACTS`、
  `materials.py:79` 的 `index_path`、`project.py` / `nf_api.py` 返回体里的
  `"path": "config/project.yaml"` —— 要么是**模块级常量**（在 `Path(ROOT) / X` 处拼接，
  如 `missing_artifacts`、`_log_audit`、`scraps_dir_path`），要么是**展示字符串**
  （告诉用户"去改哪个文件"），都不参与文件读写。
- `_resolve_root()` 里的 `Path(__file__).resolve().parents[1]`：**代码根**语义，不是数据根。

### 12.3 护栏（新增 `tests/unit/test_root_path_discipline.py`，9 断言）

- **A 静态**：相对数据路径字面量**不得裸当函数实参** —— 该行必须出现 `ROOT`，或显式
  `# noqa: root: <理由>`。用 AST 判据（只查 Call 的实参），不是 grep，所以
  `STAGE_ARTIFACTS` 这类常量表不会被误报。
- **B 静态**：`global ROOT` 只允许出现在 `_set_root()` 内。
- **C 功能反证**：`_set_root(夹具)` 后 `GLOBAL`/`HISTORY_DIR` 必须跟着走、`build_state()`
  必须读到**夹具书名**；复原后必须读回主项目 —— 证明前一条不是"碰巧读到夹具"。
- **D**：`noqa: root` 必须带理由，防止当万能挡箭牌。

**反向验证（证明断言有效，不是"本来就好"）**：把 A/B 两处回退成历史形态后，护栏分别打红并
报出 `nf_api.py:679  ProgressManager("data/state/progress.json")` 与
`nf_api.py:1000  act_approve()`；还原后重新全绿。

**回归**：`nfctl test` **44 文件 / 44 通过**；e2e 视觉验收 **74 通过 / 0 失败**。

---

## 13. 定价批量导入（2026-09-30，暮雨点名要的功能）

### 13.1 做了什么

定价面板（09-23 上线）此前**只能逐行手工编辑**，缺的正是"整表粘贴"。本次补上，
走**两段式**（预览 → 确认）：

- **解析**（`utils/cost_tracker.parse_rates_text`，纯函数）自动识别三种写法：
  ① JSON（dict-of-dict / list-of-dict / 数组值 / 中文键 / 顶层单条）；
  ② **Python 字面量** —— 直接把 `cost_tracker.py` 里的 RATES 块整段复制即可；
  ③ 表格行「模型 输入价 输出价 [缓存价]」，分隔符任意（空白/制表/逗号/竖线/顿号），
  吃货币符、千分位、`元 / 百万 tokens` 这类单位尾巴、注释行、表头行、`免费`。
- **合并策略**（`plan_rates_import`）：**upsert** —— 只覆盖同名条目，绝不删其他；
  缺 `cache_read` 时**沿用该模型现有缓存价并告警**（静默置 0 会把缓存命中当成不花钱、
  账单偏乐观），现有条目也没有才置 0。
- **落盘**（`upsert_custom_rates`）：写 `data/state/cost_rates.json`，落盘前把原文件另存
  `cost_rates.json.bak`。
- **端点** `POST /costs/rates/import`：`confirm=false` 只解析预览（零写入）/
  `true` 才落盘；**存在解析错误一律拒绝写入**（400，附全部错误）。
- **界面**：定价面板加「📋 批量导入」粘贴框 + 预览表（模型 / 动作 / 现有 → 导入后）
  + 错误块 + 警告块；有解析错误时「确认导入」置灰。

**纪律**：跳过任何一行都必须留痕 —— 识别不了的进 warnings、语义错误进 errors，都带行号。

### 13.2 验收

- `tests/unit/test_rates_import.py`：**53 断言**。除正向解析外带反证：预览期文件**逐字未变**、
  有解析错误时 `confirm=true` 仍拒绝写入且文件不变、upsert 不删其他条目、备份内容逐字等于导入前原文。
- e2e 新增 **10 条**（开面板 → 预览 → 报错块 → 确认落库）；`tests/e2e/mock_nf_api_state.py`
  的对应端点**复用真实解析器**，不在 mock 里再写一份判据。

### 13.3 遗留

CLI 侧 `scripts/price_wizard.py` 仍是逐个模型问答式（写的是**源码 RATES**，与 GUI 写自定义
定价是两条不同路径）。若也想让它吃批量粘贴，直接复用 `parse_rates_text` 即可 —— 未做，等你发话。

---

## 14. MCP stdio 垫片（2026-09-30，执行单 ⑤前置步0 **完成**）

### 14.1 问题

`nf_mcp.py` 跑在 **TCP 8766**，用的是「TCP + 换行分帧的裸 JSON-RPC」——
**不是** MCP 标准的 stdio 传输。标准 MCP 客户端（Hermes / Claude Code / Cline）
只会在本地 **spawn 进程**、用 stdin/stdout 说话 → **直连不上 8766**。
此前仓库里只有 `nf_mcp.py` 文件头的一句「中间必须有 stdio 垫片」，**垫片本身不存在**。

### 14.2 交付物

`scripts/nf_mcp_stdio_bridge.py` —— stdio ↔ 8766 双向透传（两侧都是换行分帧 JSON-RPC，
逐行搬运即保真）。真正的价值在**搬不动的时候怎么说话**：

| 场景 | 行为 |
|---|---|
| 8766 连不上 | 返回 **`-32002` + 层号 + 可执行启动指引**，`id` 保真（客户端会把它当该请求的响应） |
| 非法 JSON 输入 | 回 `-32700`，进程不退，后续请求照常 |
| 空行 | 忽略（不是错误，也不产生响应） |
| 8766 后起 | **惰性连接**：下次请求自动接上（客户端常驻，服务不一定先起） |
| 客户端 EOF | 收尾退出，向 8766 发 FIN |

**三条硬纪律**（都写进了文件头）：
1. **stdout 只走协议** —— 日志一律 stderr。往 stdout 写一行非 JSON，客户端就废了。
2. **分帧缓冲必须留在实例上** —— 一次 `recv` 常带回多行，就地拆行会把**半行**当整行发出去，
   那是静默的错误帧（客户端只看到解析失败，不会告诉你是我拆坏的）。
3. **不解析、不改写 payload** —— 透传保真，唯一例外是本地送不出去时按同 `id` 回错误。

### 14.3 自检与反向验证

`tests/unit/test_mcp_stdio_bridge.py`（**15 断言**，全离线：本地 mock 当 8766）——
覆盖透传保真、**通知（无 id）不阻塞后续请求**（按"请求-响应"严格配对的实现会在这里死锁）、
非法输入、空行、不可达文案、**惰性重连**、stdout 纯净。

**反向验证**：破坏「拒连错误码」与「连接目标端口」→ 自检分别打红；还原后全绿。

**过程中揪出一段假绿并删除**：原实现里有「发送失败后立即重连再试一次」的二次尝试，
反向验证时发现**把它改坏自检照样全绿** —— 因为 socket 半开时 `sendall` 往往**成功**
（数据进内核缓冲，随后才收到 RST），那个分支几乎不触发。留着只会让人误以为有保护，
**已删**，并在 docstring 里写明为什么不做。

### 14.4 未做（需真机）

**⑤前置步1「stdio 垫片真机验证」**：需要 Hermes（或任一标准 MCP 客户端）
实机 spawn 垫片、完成标准握手并列出 ~22 个 `nf_*` 工具 —— **2026-09-30 已用官方 MCP SDK
完成等价验证（7/7，见 §15.1）**；剩下的只是「Hermes 本体加载我们这条配置」这一步，
需要你本机操作（步骤同样在 §15.1）。

---

## 15. 保障事项盘点 + spawn 验证的可操作方式（2026-09-30）

### 15.1 spawn 验证：三级，从便宜到彻底

**关键发现：Hermes 就装在这台机器上**（`%LOCALAPPDATA%\hermes\bin\hermes.exe`），
且它的 `config.yaml` 里 `mcp_servers:` 段**已经有 stdio 型条目**（`cua-driver` 用
`command` + `args` + `enabled`）—— 也就是说它的 spawn 通道本来就是通的，
我们要做的只是「加一条配置」。

| 级别 | 怎么做 | 证明什么 | 实测结果 |
|---|---|---|---|
| ① 离线自检 | `python tests/unit/test_mcp_stdio_bridge.py` | 垫片自身行为（透传/通知/错误/重连/stdout 纯净） | **15/15** |
| ② 真机握手 | `python scripts/nf_mcp_handshake_check.py` | **标准协议栈 × 真服务**：起真 `nf_api`(18765) + 真 `nf_mcp`(18766)，用**官方 MCP SDK** 的 `ClientSession` 走完 `initialize` → `tools/list` → `tools/call` | **7/7**（`novelforge-mcp 0.1.0` · 协议 `2024-11-05` · **22 个工具** · `tools/call` 返回真实书档） |
| ③ 客户端实测（**最终判据**） | 在 `%LOCALAPPDATA%\hermes\config.yaml` 的 `mcp_servers:` 下加一条（见下），备份该文件，让 Hermes 重新加载，然后问它「列出 novelforge 的工具」 | 目标客户端真的能 spawn 并调用 | **需你操作**（我不动你的 Hermes 配置） |

③ 的配置片段（`<项目根>` 换成实际路径；Hermes 同段已有 stdio 条目可对照）：

```yaml
  novelforge:
    command: <项目根>\.venv\Scripts\python.exe
    args:
      - <项目根>\scripts\nf_mcp_stdio_bridge.py
    enabled: true
```

⚠️ 改前先备份 `config.yaml`；改完需让 Hermes 重新加载配置。完整步骤、
其余客户端（Claude Code / Cline / Cursor）的 `mcpServers` JSON 写法、
以及**排错对照表**（哪种报错该去修哪一层）见 `docs/mcp-connect.md`。

> 另有一条更省事的握手验证：MCP 官方 SDK 自带 CLI 客户端 ——
> `python -m mcp.client <项目根>/.venv/Scripts/python.exe <项目根>/scripts/nf_mcp_stdio_bridge.py`
> （需装 `mcp` 包；Hermes 的 venv 里就有）。

### 15.2 本轮**修掉**的保障缺口

| 缺口 | 风险 | 处置 |
|---|---|---|
| `playwright` / `mcp` 未登记进 `requirements-dev.txt` | 干净克隆上「发版闸门」与「握手自检」会**静默跳过** —— 跳过看起来像通过 | 已补登，并注明「不装 = 闸门静默跳过，不是通过」 |
| 「发版前必跑」只是文档里的一句话 | 靠人记 → **已经漏跑过一次**（e2e 长期潜伏红色到 09-29 才发现） | 新增 `nfctl release-check`（五项一条命令）；首跑即抓到真问题 |
| 打包产物可能落后于工作区 | 「打了包但包里是旧代码」→ 用户装了也不生效 | release-check 里的**产物核验**：payload 关键脚本 sha256 必须与工作区一致 |
| MCP 接入没有操作文档 | 用户拿到垫片也不知道怎么接 | 新增 `docs/mcp-connect.md`（三步接入 + 三级验证 + 排错表） |

### 15.3 仍需**你一句话**的保障事项

| # | 事项 | 现状 / 风险 | 我的建议 |
|---|---|---|---|
| 1 | ~~25 个提交只在本地~~ **✅ 已 push** | 此前一天半的工作只存在这台机器上（磁盘坏 = 全丢） | 已完成：`origin/main` = `46d6bb2`。**push 前体检**：这 25 个提交不含 `.env` / `data/` / `materials/`（唯一的 `data/` 是 e2e 夹具的假状态文件） |
| 2 | **无 CI**（`.github/workflows` 不存在） | 回归全靠手动；e2e 那次长期红就是例证 | 可加一个只跑 `nfctl test` 的 workflow（不跑 e2e —— 它要 playwright + 端口，CI 上不稳定）。push 通道已通，加了就能生效 |
| 3 | **备份范围不含 `config/`、`materials/`** | `switch_book --archive` 只搬 `data/` 8 项 → 换书丢配置与素材卡 | 把两者纳入归档，或做真正的导出/导入包（§10）。**涉及搬用户数据，等你点头** |
| 4 | `Temp/` 有 120 项 15MB 一次性脚本残留 | 已 gitignore，不影响仓库；只是越积越多 | 可加一条「可随时清空」说明；我不擅自删（里面有你的截图） |
| 5 | `check_consistency.locked_violations` 未实现 | 「locked 不可违逆」目前**只在提示词层**，没有确定性检查 | 可做「locked 条目在产物缺失/被改名」的确定性检查（不做语义冲突判定 —— 机器判不准，会变噪音） |
| 6 | `kimi-k2.6` 的真实刊例价未落 | 源码 `RATES` 里是**占位价** → 账单绝对值不可信 | 现在有两条路：面板「成本→定价→**批量导入**」粘贴落库（不动源码），或把三个数给我改 `RATES`（对所有环境生效） |

**已确认无问题**：`.env` 已被 `.gitignore` 忽略且未入库；`requirements.txt`（运行期）版本已精确锁定；
日志有轮转（`logging.rotate_days`）；`data/state/truncated` 当前为空（截断隔离没堆积）。


