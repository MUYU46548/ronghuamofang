# 二号测试用例：《炭城纪》

与 `examples/sample-book/`（《雾港核心》）并列的第二套夹具书。**不进安装包、不是出厂样例**，
只服务于回归测试与手工试跑。

## 为什么要有它

2026-10-18 发现：`examples/sample-book/` 的素材卡被整套换成《雾港纪年》宇宙后，
`user_outline` 没同步 → 大纲讲沈砚去雾港遗址，设定集里全是潮神与鲸民。
一本 3 章的书拿到 87% 属于别人的设定集，全程门禁绿灯。

`test_sample_book_coherence.py` 只锁住了「第一套素材与大纲配套」。一套夹具不够：
**流水线可能对某一套专名过拟合**（提示词、模板、抽取规则都可能在那一套上恰好成立）。
所以这里用第三个虚构宇宙（《炭城纪》，窑腔文明）再造一套**同样自洽**的素材，
用于验证「换一套完全不同的专名，判据照样成立」。

## 宇宙设定（全部虚构）

炭城建在死火山的窑腔里，腔外是「盐蚀」——把一切概念析成盐的现象。
神「窑主」造出窑腔与陶民，离去前把力量封进窑心。文明靠「陶律」运转，
集体无意识的集中会加速盐蚀、触发「窑崩」。

## 目录

| 路径 | 内容 |
|---|---|
| `project.yaml` | 书名 / `user_outline` / 素材目录（`materials/raw`） |
| `materials/raw/` | 19 张素材卡（角色 5 / 组织 4 / 场景 2 / 概念 3 / 物品 1 / 稿件 5 等） |

## 判据

`tests/unit/test_sample_book_2_coherence.py` 钉住 6 条：卡数、大纲实体全部有落点、
旧宇宙零词重叠（含《雾港核心》与《雾港纪年》两套）、每卡带脱敏声明、
首行标题合法、卡内无本机绝对路径。

## 手工试跑

```bash
cp tests/fixtures/sample-book-2/project.yaml config/project.yaml
cp tests/fixtures/sample-book-2/materials/raw/*.md materials/raw/
python scripts/orchestrator.py
```

试跑会覆盖 `config/project.yaml` 与 `data/`：**先在干净工作区做**，跑完用
`examples/sample-book/README.md` 的复位步骤还原。
