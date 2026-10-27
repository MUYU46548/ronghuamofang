# 章节编辑器与审阅台（v0.7.6）

面向「以后还要往里加模块」的接线说明。设计理由写在代码注释里，这里只讲**形状**：
有哪些端点、前端怎么复用、加新东西时照哪个范式。

## 一、为什么新开一个域模块

`/chapters/*` 在本次之前有 7 个端点，散在 `misc.py`（quality / verify）和
`refine.py`（paragraph 四件套）。按 `AGENTS.md` 的纪律「某类端点超过 3 个就另开一个
域模块，不要堆回 `nf_api.py` 的 elif 链」，这次把「正文读写」收进
`scripts/nf_api_domains/chapters.py`。

**它和 `refine.py` 的分工**（最容易混的一处）：

| | `chapters.py` | `refine.py` / `refine_chapter.py` |
|---|---|---|
| 谁改 | **用户手改**，零 token | 模型改稿，烧 token |
| 备份 | `data/chapters/history/chNN_vM.md` | 同一个目录、同一个格式 |
| 阶段状态 | 完全不碰 | 也不碰 |

两者共用备份格式是有意的：**回退只有一条通道**。两套历史必然漂移。

## 二、端点形状

### `GET /chapters/layers?n=3`

```json
{
  "ok": true,
  "n": 3,
  "current_layer": "refined",
  "last_modified_layer": "refined",
  "target_words": [5000, 9000],
  "layers": {
    "raw":     { "exists": true, "words": 2410, "title": "第3章 井底来的箱子", "mtime": 1792… },
    "checked": { "exists": true, "words": 2398, "title": "…", "mtime": 1792… },
    "refined": { "exists": true, "words": 2508, "title": "…", "mtime": 1792… }
  }
}
```

`current_layer` = **最高存在的层**（refined > checked > raw），不是 mtime 最新那层。
理由：三层是同一条产物的三个加工阶段，要审阅的是成品层；用 mtime 裁决会在
「三份同一秒写盘」时随机落到 raw（真实流水线里三层 mtime 常常只差毫秒）。

### `GET /chapters/content?n=3&layer=refined`

返回 `content`（全文）+ `chars` / `words` / `title` / `mtime` / `target_words`。
层不存在 → **404**，不返空串冒充成功。

### `POST /chapters/save`

```json
{ "n": 3, "layer": "refined", "content": "…", "expected_mtime": 1792.123 }
```

四道护栏：

1. `expected_mtime` 不一致 → **409**（编辑器开着时流水线刚重跑完这章，绝不覆盖）
2. 缺 `## 第N章` → 按 `chapter.title_format` **自动补**（用户常只贴正文）
3. 落盘前跑 `utils/text_norm`（标点 + 章标题归一化，幂等）
4. 写前把旧稿备份进 `data/chapters/history/`

**保存 ≠ 通过**：不改 progress、不打分、不跑体检。下游成品要重跑阶段 7/8 才会更新。

`GET /chapters/paragraphs?n=3&layer=refined`（在 `refine.py`）本次加了 `layer`：
不带 layer 时保持旧的「refined > checked > raw 逐层回退」，带了就**只认那一层**。
段落批注必须同层 —— 三层段落数常常不同，让批注作用在另一层上一定会改错段。

## 三、前端怎么复用

| 文件 | 角色 |
|---|---|
| `console/src/ChapterEditor.vue` | 正文编辑 + 段落批注。`embedded=false` 是抽屉（章节列表里点「编辑」），`embedded=true` 是内联（审阅台中栏）。**同一份实现**，避免两份代码漂移。 |
| `console/src/ChapterDesk.vue` | 三栏审阅台：左章列表 / 中 `ChapterEditor` 内联 / 右本章大纲 + 七维评审。挂在「章节」页签，用 `chapterView` 在列表与审阅台之间切换。 |

`ChapterEditor` 对外只有三个 props：`api`（App 的 `api()` 函数）、`n`（章号）、
`embedded`。事件：`say`（提示）、`close`（抽屉关闭）、`saved`（保存成功，携带章号）。

## 四、加新端点 / 新组件的范式

**加端点**：

1. 实现写进对应域模块，**返回 `(status, payload)`，不要碰 `h._send()`**
   （由分发器统一发响应，`test_api_domains.py` 守护）
2. 域模块里取 `nf_api` 的模块级名一律 `import nf_api as api` + `api.ROOT`，
   **不要** `from nf_api import ROOT`（那会把 ROOT 拷成死值，`--root` 失效）
3. 路径 IO 一律 `api.ROOT / …`，不写相对路径
4. 在 `nf_api.py` 的 `do_GET` / `do_POST` 加**一行**转发
5. 补 `ROUTES` 条目

**加组件**：新面板做成独立 `.vue`，通过 props 拿 `api`，通过 `emit("say")` 反馈；
不要继续往 `App.vue`（已 6600 行）里堆逻辑。

## 五、自检

```bash
python tests/unit/test_chapter_editor.py     # 48 断言：后端四道护栏 + 前端接线 + 文案无直角引号
python scripts/nfctl.py release-check        # 发版前全套（含 e2e 视觉验收）
```

`test_chapter_editor.py` 的【F】【G】两段是**防回潮**静态断言：组件被误删、
「编辑」入口被拿掉、GUI 文案里又出现直角引号，都会在这里变红。
