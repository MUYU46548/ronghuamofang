# 雾港核心（固定测试示例工程）

本目录是绒花墨坊的**固定测试示例项目**：书名「雾港核心」，奇幻、3 章短篇、
素材与配置一次配齐。用途：试跑流水线 / 验收 GUI / 重装后恢复 ——
每次从同一已知起点出发，凡是与本目录不符的产物一律视为残留。

## 组成

- `materials/raw/` — 19 张结构化素材卡。
  **就放在 `raw/` 层**（2026-10-24 修正）：`scan_materials` 只递归 `materials/raw/`，
  卡直接躺 `materials/` 根时 stage1 一张都扫不到 —— 拿样例试跑会归并出空设定集。
  出厂预设（`examples/presets/*/materials/raw/`）一直是这个层级，只有最老的样例没跟上。
- `project.yaml` — 预设项目配置（书名 / 类型 / 章数 / user_outline，快照于 2026-10-06）

## ⚠️ 大纲与素材必须配套（2026-10-18 补）

`project.yaml` 的 `user_outline` 与 `materials/` 讲的是**同一套设定**：
沈砚 / 程砧 / 祝鹤川 / 机关术 / 雾港核心 / 七锁回环 / 玄翎卫 / 机关黑市。

历史上这一条被破坏过一次：2026-10-03 提交 `91b6c78`「发布树虚构化」把 19 张素材卡
整套换成了另一个宇宙（潮神 / 鲸民 / 守灯会），`user_outline` 未同步。后果是拿本样例书
试跑时，stage1 归并出的设定集 87% 与大纲无关 —— 写沈砚的章喂进去的却是潮神的设定，
成品质量崩坏，而 `nfctl check` 与全流程门禁**全程绿灯**（没有任何判据检查二者是否配套）。

现在由 `tests/unit/test_sample_book_coherence.py` 守住：改本目录的素材或大纲，
必须保证大纲里的专有名词在素材卡里都有落点，且不得残留旧宇宙的词。

**改素材或改 `user_outline` 后请跑一次**：
`python tests/unit/test_sample_book_coherence.py`

## 复位到干净起点

> 涉及清空运行产物，属破坏性操作：先快照、再执行；拿不准先停下来问。

1. 快照：`python scripts/snapshot.py`
2. 铺素材：把 `materials/` 复制进 `materials/raw/`（覆盖）
3. 写配置：把 `project.yaml` 覆盖到 `config/project.yaml`
   （更安全的做法是走定向改写：GUI 设置页，或 `scripts/utils/project_config.py`）
4. 清运行态：删 `data/state/progress.json`、`logs/runs.db`，
   以及 `data/outline/`、`data/chapters/`、`data/setting/` 下的产物
5. 重跑：`python scripts/orchestrator.py`（退出码 0=完成 1=失败 2=熔断 3=等审批）

## 工作态纪律（试跑翻车根因，2026-10-06 定）

- **源码态**（本仓库根）与**安装版**（`%APPDATA%\绒花墨坊\workspace`）两态不混用；
  试跑前用 `python scripts/nfctl.py status` 的 project_dir 确认在哪一态，全程只用该态的正常入口。
- **运行产物只有 orchestrator 写**：手工补 `setting.json` / `progress.json` / `runs.db`
  进不了进度系统 —— 客户端永远显示待跑、下游闸门打不开，等于试跑不合格。
- 收到含 `{{...}}` 未替换占位符的提示词 = 拿到的是模板不是任务，停下报告，不要自行发挥。
