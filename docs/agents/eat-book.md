# 吃书：把一本写完的书变成正典（2026-10-27 集成自 SP-1024）

## 为什么有这个东西

绒花墨坊的设定集此前只有**一个来路**：`materials/` 里的素材卡 → 阶段 1 归并 →
`data/setting/setting.json`。可作者手里常常已经有一本**写完的书**（旧稿、前作、
想接着写的参照作品），要续写就得先把它的角色、伏笔、时间线、设定抽出来。

这正是沙盒工程 `SP-1024`（吃书工具箱）干的事：九模块零网络 Python 实现。
原来的跑法是「出提示词 → 人工贴到外部平台 → 回收响应文件」（因为沙盒没有模型）；
本仓有 `make_client()`，所以改成一条命令、进程内跑完。

## 两条命令

```bash
# 1. 拆章建目录（默认正则认「第X回/章/卷」；至少 2 个标题才拆）
python scripts/eat_book.py init 旧稿.txt --name old-book [--pattern ...] [--max-chapters N]

# 2. 跑管线：② 摘要 → ③⑥⑧⑨ 聚合 → ① 一致性 → ⑦ 病灶
python scripts/eat_book.py run old-book --dry-run     # 零 token 预演（报规模与调用数）
python scripts/eat_book.py run old-book               # 真跑（走 config/system.yaml 的模型）
python scripts/eat_book.py status old-book            # 各表条数 / 已摘要章数 / 花费

# 3. 并进设定集（只增不改，默认预演）
python scripts/eat_book.py merge old-book             # 预演：列出会加什么
python scripts/eat_book.py merge old-book --yes       # 真写（先备份 history/）
```

## 产物（`data/eatbook/<name>/`）

| 文件 | 内容 |
|---|---|
| `book.txt` / `chapters/0001.txt` | 原书与逐章切分（**只读原件，不改写**） |
| `summaries/0001.json` | ② 章节摘要（**断点在这里**：有它就算这章做完了） |
| `characters.json` | ③ 角色卡：身份 / 性格 / 关系 / 状态 / 出场章节 / 未决线 / 矛盾清单 |
| `settings.json` | ⑨ 设定卡：设定名 / 类别 / 内容 / 确立章节 / 补充章节 / 矛盾点 |
| `timeline.json` | ⑧ 时间线：顺序 / 时间 / 事件 / 依据章节 / 支线 |
| `foreshadow.json` | ⑥ 伏笔台账：伏笔 / 埋设位置 / 内容 / 状态 / 回收位置 |
| `conflicts.json` | ① 一致性冲突（默认只查后 1/3 章：前段还没铺开，查了是噪音） |
| `cliche.json` / `cliche_report.md` | ⑦ 病灶词（全量 + 逐章排行，零 token） |
| `report.md` | 人可读汇总（每章状态 + 各表条数 + 失败项 + 并库方法） |
| `cost.json` | 调用次数 / 输入输出 token / 估算金额 |

## 三条判据（都由 `tests/unit/test_eat_book.py` 钉住）

1. **断点续跑**：每章摘要落盘即算完成，重跑跳过（`--force` 才重做）。
   一章失败不影响其它章，失败项在报告里列明 —— 重跑只补没做的。
2. **并库只增不改**：`merge` 不覆盖用户已有的角色/设定（同名的一个字都不动），
   只往 `world` 四槽 / `plot_fragments` / `timeline` 追加不存在的行；
   写前备份 `data/setting/history/setting_v<时间戳>.json`。
3. **零 token 可预演**：`--dry-run` 只拆章与报调用数，一个请求都不发。

## 边界（如实说）

- **需要 `engine: direct`**：管线走 `client._post_chat()`（任意提示词调用）。
  Hermes 子会话没有这个入口 —— 那种引擎下只能用 `--dry-run` 出规模，
  或先切直连。报错会直说，不会静默降级。
- **不自动写正典**：吃出来的卡先落在 `data/eatbook/`，`merge` 是显式动作。
  理由与「回写设定库只进沙盒」同源：哪个设定更对是创作决策。
- **只检测不改写**：⑦ 不给改写建议，①/③/⑨ 的矛盾只单列，不替作者取舍。
- **⑦ 在验收里只报告不阻断**：词表命中 ≠ 写坏（"微微一笑"一章一次很正常）。
  `quality_checklist` 末尾会打一段逐章密度；要当闸门请自己看密度与排行。
- **提示词逐字来自 SP-1024**（`SP-1024/prompts/0*.md` 与代码内模板一致）。
  改其中一边就要同步另一边，否则「出门版」和「集成版」会漂移。
- **未移植**：④ 开篇仿写、⑤ 扩写器（本仓已有「开局方案」与 stage4 定向扩写，
  功能重叠）、`campaign` 的 emit/ingest 人机通道（本仓不需要）。

## 与既有模块的分工

| 需求 | 用哪个 |
|---|---|
| 素材卡 → 设定集 | 阶段 1（`stage1_consolidate`），**不是**吃书 |
| 写完的书 → 正典四表 | `eat_book.py`（本文） |
| 章大纲「悬念钩子」→ 伏笔台账 | `utils/foreshadow.py`（零 token，按大纲） |
| **正文** → 伏笔台账 | `eat_book.py` 的 ⑥（吃摘要，要模型） |
| 病灶词 / 口水话 | `nfctl cliche`（零 token）或 `quality_checklist` 末尾那段 |
