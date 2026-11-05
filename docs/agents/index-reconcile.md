# 索引与实物对账（`index_reconcile`）

**一句话**：素材目录里的登记表（`00_大纲收束索引.md`、`碎片填充计划.md`）声称的篇数与日期，
和盘上的文件对不上时，把差异摊出来 —— 零 token、只读、以实物为准。

## 为什么要有它

试跑实测：

- `00_大纲收束索引.md` 声称 **96 篇 / 9 维齐全**，`materials/raw/` 实际 **41 篇**；
- `碎片填充计划.md` 登记 **24 个日期**，碎片目录里 **18 个**日期，且**无一重合**。

**管线本身不受影响**：`stage1_consolidate.scan_materials` 是扫盘（`rglob` + 过滤），
元文件由 `_is_meta_file` 排掉，不进归并。所以这不是“数据坏了”，是**元文件会骗人**：
作者看索引以为 96 篇齐了，于是不再补素材，最后 41 篇跑出一本薄书。

对账的作用就是把这句话拆穿，并且明确说“以实物为准”。

## 判据（全部确定性，零 LLM）

| 项 | 怎么算 | 判不一致 |
|---|---|---|
| 篇数 | 元文件里 `(\d+)\s*(篇\|个碎片\|条碎片\|张卡\|份素材)`，取**首次出现**（登记表习惯把总数写在开头） | 与**同目录**实物篇数不等 |
| 日期 | 元文件全文抽 `YYYY-MM-DD` / `YYYY年M月D日` / `M月D日` / `YYYYMMDD`；实物取**文件名**，无则正文前 5 行 | 声称的日期在**全库**实物里找不到，或实物有日期未被登记 |
| 维度 | 只抽取并回显（“9 维齐全”） | **不判**：文件系统证明不了维度完整性，如实标 `dimensions_verifiable: false` |

### 两条必须记住的口径

1. **日期跨目录比**。`碎片填充计划.md` 常放在 `materials/raw/` 里，却登记的是**碎片**的日期。
   就地比会假报“无一重合”（用户实测就是这么被误导的）。所以日期拿全库实物（raw + scraps）比，
   篇数仍就地比（篇数指的是它所在目录的素材数）。
2. **mtime 不参与**。碎片的修改时间在复制/解压后会集体变，用它比会制造假差异。

## 代码位置（改之前先看这里，别另写一套）

| 层 | 文件 | 职责 |
|---|---|---|
| 判据（单一实现） | `scripts/utils/meta_detect.py` | `is_meta_file` + `iter_material_files`：后缀 / `_` 子目录 / 元文件三条规则 |
| 对账核心 | `scripts/utils/index_reconcile.py` | `reconcile(root, scopes)` → `{verdict, actual, meta_files, summary}` |
| CLI | `scripts/index_reconcile.py` | 人读 / `--json`；退出码 0 一致·1 不一致·2 读取失败 |
| Agent 入口 | `nfctl reconcile [--json]` | 同一份数据，退出码同上 |
| HTTP | `GET /materials/reconcile` | `nf_api_domains/materials.py` |
| 界面 | 素材页顶部横幅 | 不一致 → 警示 + 可展开明细；一致 → 一行淡显；无登记表 → 不显示 |

⚠️ **`is_meta_file` 只许有一份**。stage1 归并和对账若各写一套过滤，就会出现
“归并吃了 43 篇、对账说 41 篇”，作者不知道该信哪个。`stage1_consolidate` 现在
`from utils.meta_detect import ...`，并保留 `_is_meta_file` 别名给既有用例。

## 判据边界（别越界）

- **不改任何东西**：只读、零写盘。要让元文件对齐实物，是作者自己更新索引，程序不代劳。
- **不猜维度**：“9 维齐全”只在报告里回显一句“文件系统证明不了，只能人工核对”。
- **不把“没东西可比”说成“对上了”**：没有登记表时 `verdict = no_meta`，与 `consistent` 分开。
- 元文件判据是**保守**的（见 `meta_detect.is_meta_file` 的注释）：宁可漏跳一张登记表，
  也不误跳一张真素材 —— 后者那张卡永远进不了设定集。

## 用例

- `tests/unit/test_index_reconcile.py`：声称 10 / 实物 3 → 差 -7；跨目录日期命中；
  元文件不算实物篇数；一致不误报；无登记表 → `no_meta`；CLI 退出码。
- `tests/http/test_materials_reconcile_api_http.py`：端点形状 + **只读性**（请求前后素材文件不变）。
- `tests/unit/test_stage1_meta_skip.py`：元文件判据的正反向锁（真设定文件不得被误跳）。
