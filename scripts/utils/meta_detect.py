# -*- coding: utf-8 -*-
"""素材目录判据：哪些文件是“元文件”、哪些算“真素材”（零 LLM，单一实现）。

## 为什么必须只有一份

`stage1_consolidate`（归并前扫盘）和 `index_reconcile`（索引与实物对账）都要回答
“这个目录里到底有几篇素材”。两边各写一套过滤，就会出现“归并吃了 43 篇、对账说
41 篇”的分裂 —— 作者不知道该信哪个。所以判据住在这里，两边都 import。

## 三条判据（与 2026-10-24 的实证一致）

1. **后缀**：只认 `.txt/.md/.markdown/.docx`；PDF 不支持，由调用方告警。
2. **目录**：跳过 `_backup/`、`.hidden/` 这类 `_`/`.` 开头的**子目录**。
   `_backup/` 装的是同名覆盖前的旧版本，误扫等于把同一张卡的两个版本都算进去。
3. **元文件**：`碎片填充计划.md`、`00_大纲收束索引.md` 这类**描述素材**的登记表，
   不是素材本身。喂进归并会把索引当设定，污染设定集。

第 3 条是**保守**判据：只认明确的登记类词，不猜“看起来像索引”的东西。宁可漏跳
一张元文件（归并时当噪声，代价小），也不能误跳一张真素材（那张卡永远进不了设定集）。
"""
from pathlib import Path

SUPPORTED_EXTS = {".txt", ".md", ".markdown", ".docx"}
PDF_EXTS = {".pdf"}


def is_meta_file(path):
    """判断素材文件是否为“元文件”（描述素材的登记表，而非素材本身）。

    判据（确定性，零 LLM）：文件名**或**正文前 2000 字里出现登记/说明/计划类
    标题词。为什么按内容判而不是只看文件名：用户随手命名（如 `碎片填充计划.md`
    正文是“已使用的碎片在此登记”）无法靠文件名穷举，标题词才稳。
    """
    path = Path(path)
    # 文件名本身就是登记类：`README.md` / `读我.md`。为什么单列一条：仓库自带的
    # `materials/raw/README.md` 标题写的是 `# materials/raw/`（不是“Readme”），
    # 下面全是“此目录默认空 / 格式 / 快速上手”的目录说明 —— 靠标题词认不出来，
    # 会被当成素材喂进归并（2026-11-05 对账时实测）。
    # ⚠️ 只认 readme / 读我，**不认“说明”**：`说明.txt` 在既有夹具里是真素材，
    #    按名字一刀切会把它踢出归并（test_stage1_scan S5 当场变红）。
    if path.stem.strip().lower() in ("readme", "读我"):
        return True
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return False
    probe = (path.stem + "\n" + head).lower()
    # “填充计划”“变更日志”“登记列表”“索引”“说明”等元文件信号词。
    # “说明”单独出现太宽（真卡也可能写"说明"），只在**标题行**匹配（行首 # 或开头）。
    # 跳过 YAML frontmatter：设定库 真设定文件（如 `地理系统索引.md`）以
    # `---` 包裹 tags，里面有 `- 索引` 这种 **tag 行**。我第一版把 `#`/`-`
    # 开头的行都当标题扫，结果 6 个真设定被判成元文件（2026-10-24 反向验证
    # 抓出）。frontmatter 只描述元数据，不是标题，必须先剥掉。
    lines = head.splitlines()
    body = lines
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 40)):
            if lines[j].strip() == "---":
                body = lines[j + 1:]
                break

    # “XX索引”类标题：**只跳自动生成的聚类清单**，不碰真设定。
    # 2026-10-24 反向验证抓出的坑：设定库 `01 索引/` 下 13 个真设定文件
    # 标题是“世界索引”“历史事件”“幻境”“角色池”，文件名也带“索引”
    # —— 我第一版按"标题尾部是索引"判元，把它们全误伤了。真设定的标题
    # 讲的是**世界里的东西**，元文件的标题讲的是**这批素材本身**（聚类/
    # 收束/总览/未改动源文件）。所以只认后者，且必须同时命中“自动生成”
    # 或“收束/聚类/总览”这类自我指涉的词。
    for line in body[:12]:
        s = line.strip().lstrip("#").strip().lower()
        if not s:
            continue
        if s in ("说明", "登记列表", "索引", "目录", "变更日志",
                 "碎片填充计划", "使用说明", "读我", "readme"):
            return True
        if s.endswith(("索引", "索引 v2", "索引 v1", "清单", "目录总览")) \
                and any(k in s for k in ("收束", "聚类", "总览", "维度")):
            return True
    # probe 只认**自我指涉**的元信号：这些短语只可能出现在"描述这批素材"的
    # 文件里。⚠️ `本索引` 已删除（2026-10-24 反向验证抓出）：设定库 真设定
    # `地理系统索引.md` 的正文首句就是“**本索引**遵循的层级划分规则为：”——
    # 我为了跳索引加的这个词，恰好把真设定的正文首句当成了元信号。跳索引
    # 靠标题判据（上方收束/聚类/总览），不靠正文词。
    return any(k in probe for k in ("已使用的碎片在此登记", "变更日志",
                                    "目录清单",
                                    "自动生成，未改动任何源文件",
                                    "按 9 维聚类", "反推世界支柱"))


def iter_material_files(directory):
    """遍历目录，返回 `(files, warnings, skipped_meta)`。

    规则与 stage1 归并完全一致：递归、跳 `_`/`.` 子目录、只认 SUPPORTED_EXTS、
    跳过元文件。PDF 不进 files，但在 warnings 里明确说一句（不静默吞掉）；
    被判为元文件的路径单列在 `skipped_meta`，由调用方决定怎么提示
    （stage1 打印 `[SKIP]`，对账要把它们当“声称方”读）。
    """
    directory = Path(directory)
    files, warnings, skipped_meta = [], [], []
    if not directory.exists():
        return files, ["目录不存在: " + str(directory)], skipped_meta
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            if ext in PDF_EXTS:
                warnings.append("PDF 素材暂不支持，已跳过: " + path.name)
            continue
        rel_parts = path.relative_to(directory).parts[:-1]
        if any(part.startswith(("_", ".")) for part in rel_parts):
            continue
        if is_meta_file(path):
            skipped_meta.append(path)
            continue
        files.append(path)
    return files, warnings, skipped_meta
