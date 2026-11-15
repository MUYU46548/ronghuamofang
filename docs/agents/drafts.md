# 草稿层：写在框里的东西，先存草稿、显式发送才进对话

> 适用：审稿页每条意见的**评论**、章节页**讨论**的发言、以及以后任何“输入 → 点发送 → 进一条记录”的地方。
> 代码：`console/src/draftStore.js`（唯一实现）· 判据：`tests/e2e/e2e_ux_verify.py` **K 段**。

---

## 一、为什么要这一层（2026-11-15，抄 Papilot 第 ⑥ 条机制）

Papilot 对“写一半的东西”的态度是：**评论先存草稿，显式发送才进对话**。
我们原来的样子是三种坏法叠在一起：

| 坏法 | 具体表现（改前） |
|---|---|
| 关一下就没了 | `ReviewConsole.toggleComment` 重开输入框时 `commentText[key] = ""` —— 收起（或误点）一次，写了一半的评论消失 |
| 串味 | `DiscussThreadPanel` 只有一个共用的 `sayText` —— 在主题 A 写了一半，切到主题 B 它还在，一不小心以 B 的名义说出去了 |
| 刷新就没 | 草稿只在内存里，刷一下页面、切一下页签就空 |
| 看着像说过了 | 收起后界面没有任何“还没发出去”的痕迹，作者以为已经说过 |

这四种都不花 token、也不报错，所以纯后端用例看不见 —— 只有真机 e2e 才碰得到。

## 二、做法

`console/src/draftStore.js` 一个模块，四个函数 + 一个版本号：

- `draftKey(...parts)`：**按位置**分键（`mofang_draft:comment|1|f1`、`mofang_draft:discuss|1|结局要不要开信`）。
- `saveDraft(key, text)` / `loadDraft(key)` / `clearDraft(key)`：落 `localStorage`（本机、不进 Git、不发请求）。
  文本为空时**顺手删键**（不留空壳）。
- `hasDraft(key)`：给界面判断“有没有未发送的草稿”。它读 `draftsVersion`，
  所以只在存/清时触发重渲染 —— 模板里可以当普通判断用。
- `draftsVersion`：任何一次存/清都 +1，`hasDraft` 依赖它。

三条纪律：

1. **只有发送成功才 `clearDraft`**。失败一律留着（否则要重打一遍），并且提示里写上“草稿还留着”。
2. **换位置之前先 `saveDraft` 当前那份，再 `loadDraft` 新那份**（`DiscussThreadPanel.pick`、`switchChapter`）。
3. **收起 ≠ 发送**：收起状态的界面上必须有“有草稿，还没发送 / 草稿，还没说出去”的标记，
   并给一个“继续写”把草稿接回来。**别让作者以为自己说过了** —— 这条是这层存在的理由。

## 三、边界（如实声明）

- 草稿只在本机 `localStorage`：换机器、清缓存就没了。刻意不做服务端同步
  （草稿属于作者没定稿的东西，进 `data/` 就等于给它一个“已提交”的身份）。
- 不做 TTL 清理：草稿是有限的几行文字，留着比悄悄删掉安全。
- 段落精修（`ChapterEditor`）、条目讨论（`EntryDiscuss`）**这轮没接**：
  它们各自的“草稿”是组件内的临时状态，行为与这里不同，别假定已覆盖。
- 输入框里按 Enter 在审稿评论里仍然是“发送”（这是显式动作）；讨论发言框里 Enter 是换行。

## 四、判据（K 段，22 条）

`tests/e2e/e2e_ux_verify.py::test_drafts`，两头都断：

- 界面：写进去就有“草稿”标记；切到另一条讨论输入框是空的；切回来草稿还在；
  **刷新页面**后草稿还在；收起评论框后出现“有草稿，还没发送”+“继续写”。
- 后端：没点发送时 `/_mock/discuss_says`、`/_mock/review_comments` **一条都没有**；
  点发送后各收到一条，界面草稿消失。

假后端为此补了 `/discuss/list|read|open|say`、`/review/report`、`/review/decisions`、
`/review/comment` 与两个 `/_mock/*` 回查端点（`tests/e2e/mock_nf_api_state.py`）。
