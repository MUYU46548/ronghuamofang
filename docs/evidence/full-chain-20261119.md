# 全链出 Word 证据（2026-11-19）

> 这是 `docs/evidence/` 的第二份证据。上一份是 2026-09-28 的 smoke，之后“全链出 Word
> 成功过 3~4 次”只存在于记忆里，没有可复跑的东西 —— `docs/evidence/` 空着这件事本身
> 就是问题总账 G1。
>
> 现在这条链有脚本：`tests/e2e/e2e_full_chain.py`，**零 token**（假引擎），
> 任何改动之后都能重跑。命令：

```
python tests/e2e/e2e_full_chain.py          # 约 2-4 分钟
```

## 结果

| 项 | 值 |
|---|---|
| 通过 / 失败 | **38 / 0** |
| 阶段 | 1→8 全过（含两道审批门、一次分段审批） |
| 章数 | 40（raw / checked / refined 各 40） |
| 合并稿 | `data/merged/book.md`，约 515 KB |
| Word 成品 | `.docx` 47 KB，`word/document.xml` 正文 **162 454 个汉字** |
| 章节标题 | “第1章 …” 进正文 |
| 日志 | 无 Traceback / PermissionError |

工作区布局与**打包态一致**：代码与数据同一个根（`scripts` / `prompts` / `templates`
+ `config` 全在工作区里，主进程 `seedWorkspace` 就是这么播的）。

## 这条链今天修掉的三个断点

跑一次就撞到三个，全都是“任何新用户第一次用都会撞”的，此前没有任何一条被记录：

### 1. 出厂配置指向一个已被移出仓库的模板（导不出 Word）

`config/project.yaml` 与 `project.yaml.example` 的 `book.word_template` 都写着
`templates/小说格式模板V1.3.dotx`。那个文件是**作者的私人模板**，2026-11-07 由
`27363c2`（“作者私人模板移出仓库”）把它删掉了，**默认值没跟着改**。

后果：stage7 报“模板不存在 → 不出文件”，整本书导不出 Word。而 stage7 的重试逻辑
还会连试 3 次（每次 3s/6s 退避），日志刷屏但结论一样。

修：默认值改为 `templates/出厂预设模板.docx`（`scripts/factory_template.py` 生成，
`--check` 合规）。作者本机的 `config/project.yaml` 同样改掉 —— 它不入库，但影响实际运行。

判据：E0 段。反证：把默认值改回旧路径 → E0 红，错误信息就是这一条。

### 2. 自由模式跑单个阶段，被“全书质量验收”误拦

`stages=[1]`（GUI 勾一道工序）跑完会触发**全书**质量验收闸门，而这时一章都还没写 →
“40 章产物缺失” → 把成功的阶段判成失败。原先只有 `--stage N`（单跑）那条路有豁免，
自由模式的 `stages=[N]` 没有。

修：口径改成“**本次跑到最后一个阶段**（或默认全流程）才做收尾验收”。

判据：E2 段（阶段 1 必须 `exit=0`）。反证：把 `_reaches_end` 改回恒真 →
阶段 1 立刻 `exit=1`，脚本红 3 条。

### 3. 假引擎在 stage6 三卷并发写同一个报告文件

stage6 是**分卷并发**润色（`polish=3`），假引擎的三卷线程都写
`data/outline/polish_report.md`，Windows 上第二个线程撞同名 `.tmp` →
`PermissionError` → 整个阶段判 crashed。

真实引擎不走这条路径（报告由 stage6 自己按卷写 `polish_report_vol{N}.md` 再合并），
但**假引擎是唯一的零 token 全链验证手段**，它一崩就没人能自验。

修：写报告时吞掉并发冲突（内容恒定“无修改”，谁写成算谁的）。

## 反证（都改源码，都真红过）

1. `config/project.yaml` 的 `word_template` 改回 `templates/小说格式模板V1.3.dotx`
   → E0 红。
2. `scripts/orchestrator.py` 的 `_reaches_end` 改回恒真（旧的“自由模式也做全书验收”）
   → 阶段 1 `exit=1`，脚本 35/3。

## 复跑方式与边界

- 跑的是**假引擎**：正文由 `utils/fake_client.py` 的静态句库拼出（94 句 / 2294 字），
  结构合法、能过全部判据，但**不代表真实模型写得好**。
- 因此本证据证明的是“**流水线结构与各阶段产物契约成立**”，不是“文风/质量达标”。
  后者要真跑，要花额度，得作者点头。
- 打包态的 Electron 窗口（弹窗、点同意、界面操作）不在本脚本范围，见
  `e2e_packaged_license.py`。

## 2026-11-20：收进一键验收，并修掉一个"外部豁免污染"

`tests/e2e/run_all.py` 新增 `full` 套件 —— 不进这份清单的"可复跑证据"没人会记得跑，
账就会烂回 G1 那种"只有一句记忆"的状态。收进去当场撞到一件事：

`run_all.py` 会给所有子进程设 `NF_LICENSE_ACK=1`（多数套件跑在从没签过字的临时工作区上），
而本脚本 E1 那条判据**正是要验真拦**（未签字 → `/state` 403）。被外层豁免污染后它变成
期望 403、实得 200 —— **假绿**，而且只在"从 run_all 进来的那条路"上假绿。
修法：子进程 env 里显式 `env.pop("NF_LICENSE_ACK", None)`，让总闸始终真验。

复跑（约 110 秒，零 token）：

```
python tests/e2e/run_all.py --only full     # 结果 38 / 0
```

