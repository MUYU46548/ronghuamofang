# -*- coding: utf-8 -*-
"""导出链校验（第一波防御 · 任务四，2026-10-22）。

施工单口径：导出统一走模板链（内容/样式/结构分离）；**导出前**先过格式校验
（样式缺失 / 结构错位 / 占位符残留），校验不过**不出文件**。

三层校验各拦什么：

1. `check_merged_md`（内容 + 结构，md 层，导出**前**跑）：
   - 占位符残留：XXX / TODO / 待补充 / {{ }} / 模板四占位符混进正文
   - 协议标记残留：`===FILE:` 等（merge_book 已剥，这里是最后一道网）
   - 本机绝对路径泄漏：E:\\ / C:\\ / /home/（成品要出门，不能带门牌号）
   - 结构错位：无章标题 / 章号重复 / 空章（标题下无正文）
   - 复杂元素残留（表格/图片/代码块）→ 警告（md_to_docx 会降级，不拦）

2. `check_template`（样式层，导出**前**跑）：
   - 配置了模板但文件缺失/不可解析 → **错误**（此前静默降级成硬编码样式，
     用户以为用了模板其实没有 —— 与 KB 注入静默降级是同一类坑）
   - 模板缺正文锚点 [请输入文本] → 错误（正文会被追加到模板末尾 = 结构错位）
   - 模板缺 Normal / Heading 1 样式 → 错误（章标题样式回退 Normal = 结构错位）
   - 缺 [书籍标题] / [作者] / [目录占位符] → 警告（对应位置留空，不出错版）

3. `check_docx_output`（成品层，导出**后**跑，不过就删文件）：
   - 四占位符在成品里仍残留 → 错误（模板替换失败）
   - 成品章标题数与 md 不一致 → 错误（结构错位）

判据刻意不认模板的具体样式表内容（字体/页面设置由 template_check.py 那套
逐字比对负责）—— 本模块只管“能不能出一份不残缺、不错位的文件”。
"""
import re
import zipfile
from pathlib import Path

from utils.file_io import read_text
from utils.verify_chapter import PLACEHOLDERS, TITLE_RE

# 模板四占位符（与 stage7_convert.PH_* 同值；此处独立声明避免循环导入）
TEMPLATE_PLACEHOLDERS = ("[书籍标题]", "[作者]", "[目录占位符]", "[请输入文本]")

# 本机绝对路径形态（成品里不该出现）
ABS_PATH_RE = re.compile(r"[A-Za-z]:\\|(?<![\w/])/(?:home|Users|Users?)/|\\\\[?.]?\\")
# 协议标记残留
PROTOCOL_RE = re.compile(r"^===(FILE|APPEND|DELETE|END)[:=]", re.M)
COMPLEX_PATTERNS = [
    (re.compile(r"^\s*\|.*\|\s*$", re.M), "Markdown 表格"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "Markdown 图片"),
    (re.compile(r"^```", re.M), "代码块"),
]
CHAPTER_NUM_RE = re.compile(r"^##\s*第\s*(\d+)\s*章", re.M)


def check_merged_md(md_text, expect_chapters=None):
    """合并稿（merged/book.md）的导出前校验。返回 (errors, warnings)。"""
    errors, warnings = [], []
    text = md_text or ""

    if not text.strip():
        return ["合并稿为空（无任何内容可导出）"], warnings

    # ---- 占位符残留（硬拦：这些词出现在成品里就是事故）----
    for ph in PLACEHOLDERS + TEMPLATE_PLACEHOLDERS:
        if ph in text:
            errors.append(f"占位符残留：{ph!r}（正文未完成或模板占位符混入正文）")

    # ---- 协议标记残留（硬拦：说明剥标记链路坏了）----
    for m in PROTOCOL_RE.finditer(text):
        errors.append(f"协议标记残留：{m.group(0)!r}（LLM 协议块未被剥离）")
        break

    # ---- 本机绝对路径泄漏（硬拦：成品要出门）----
    m = ABS_PATH_RE.search(text)
    if m:
        errors.append(f"绝对路径泄漏：…{text[max(0, m.start()-20):m.end()+20]!r}…"
                      "（成品不得包含本机路径）")

    # ---- 结构：章标题 ----
    nums = [int(x) for x in CHAPTER_NUM_RE.findall(text)]
    if not nums:
        errors.append("结构错位：合并稿里找不到任何“## 第N章”章标题")
    else:
        dup = sorted({x for x in nums if nums.count(x) > 1})
        if dup:
            errors.append(f"结构错位：章号重复 {dup}（同一章被合并了多次？）")
        # 空章：标题之后到下一个标题之间没有任何正文
        parts = re.split(r"(?m)^(##\s*第\s*\d+\s*章.*)$", text)
        # parts = [前置, 标题1, 正文1, 标题2, 正文2, ...]
        for i in range(1, len(parts) - 1, 2):
            title, body = parts[i], parts[i + 1]
            if not re.sub(r"\s+", "", body):
                errors.append(f"空章：{title.strip()[:30]} 之下没有任何正文")
        if expect_chapters and len(nums) < int(expect_chapters):
            errors.append(f"章数不足：合并稿 {len(nums)} 章 < 计划 {expect_chapters} 章"
                          "（部分章节尚未生成）")

    # ---- 复杂元素（md_to_docx 会降级为文本，不拦只报）----
    for pat, name in COMPLEX_PATTERNS:
        if pat.search(text):
            warnings.append(f"含{name}：导出时将降级为文本标记")

    return errors, warnings


def _read_docx_xml(path):
    """直接从 zip 读 document.xml / styles.xml（不依赖 python-docx 打开成功）。"""
    with zipfile.ZipFile(path) as z:
        doc = z.read("word/document.xml").decode("utf-8", "replace")
        styles = z.read("word/styles.xml").decode("utf-8", "replace")
    return doc, styles


def check_template(template_path):
    """模板可用性校验（样式层）。返回 (errors, warnings)。

    判据只认“结构必需品”：正文锚点、Normal/Heading 1 样式 —— 字体/页面
    设置的逐字比对是 template_check.py 的职责（导出后 8 点校验），别在这里重复。
    """
    errors, warnings = [], []
    p = Path(template_path)
    if not p.exists():
        errors.append(f"模板不存在：{p}（book.word_template 指向的文件丢了 —— "
                      "改配置指向别的模板，或清空该键回退硬编码样式）")
        return errors, warnings
    if p.suffix.lower() not in (".dotx", ".docx"):
        errors.append(f"模板格式不支持：{p.suffix}（需 .dotx / .docx）")
        return errors, warnings
    # .dotx 归一后读 XML（md_to_docx 走 python-docx，这里走 zip 直读：
    # 两条路独立 —— python-docx 打不开的模板，这里能给出更细的原因）
    src = p
    tmp = None
    try:
        if p.suffix.lower() == ".dotx":
            from stage7_convert import dotx_to_docx
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
            tmp.close()
            dotx_to_docx(p, tmp.name)
            src = Path(tmp.name)
        doc_xml, styles_xml = _read_docx_xml(src)
    except Exception as e:                                    # noqa: BLE001
        errors.append(f"模板不可解析：{type(e).__name__}: {e}（文件可能已损坏）")
        return errors, warnings
    finally:
        if tmp is not None:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:                                 # noqa: BLE001
                pass

    flat = re.sub(r"<[^>]+>", "", doc_xml)
    for ph in ("[请输入文本]", "[书籍标题]"):
        if ph not in flat:
            if ph == "[请输入文本]":
                errors.append("模板缺正文锚点 [请输入文本]：正文将被追加到模板"
                              "末尾（结构错位）—— 请在模板中补该占位符段落")
            else:
                warnings.append("模板缺 [书籍标题]：封面书名位置将留空")
    for ph in ("[作者]", "[目录占位符]"):
        if ph not in flat:
            warnings.append(f"模板缺 {ph}：对应位置将留空（不影响正文）")
    # 样式存在性（按 <w:name> 反查，styleId 各模板不一）
    def _has_style(wname):
        want = re.sub(r"\s+", "", wname).lower()
        for m in re.finditer(r"<w:style\b.*?</w:style>", styles_xml, re.S):
            n = re.search(r'<w:name w:val="([^"]+)"', m.group(0))
            if n and re.sub(r"\s+", "", n.group(1)).lower() == want:
                return True
        return False
    for st in ("normal", "heading 1"):
        if not _has_style(st):
            errors.append(f"模板缺必需样式 {st!r}：对应段落将回退 Normal"
                          "（章标题失去层级 = 结构错位）")
    return errors, warnings


# ---------------------------------------------------------------------------
# 逐章核对（F22，2026-11-03）：导出件与源"差异 = 0"要有报告，不是凭结构数推断
# ---------------------------------------------------------------------------

def docx_plain_text(docx_path):
    """取 docx 里的纯文本（按段落拼接）。读不出就返回 ""。"""
    try:
        doc_xml, _ = _read_docx_xml(Path(docx_path))
    except Exception:                                        # noqa: BLE001
        return ""
    paras = re.findall(r"<w:p[ >].*?</w:p>", doc_xml, re.S)
    out = []
    for para in paras:
        parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S)
        if not parts:
            continue
        txt = "".join(parts)
        # XML 实体还原（&amp; &lt; &gt; &quot; &#39;）
        txt = (txt.replace("&lt;", "<").replace("&gt;", ">")
                  .replace("&quot;", '"').replace("&#39;", "'")
                  .replace("&amp;", "&"))
        out.append(txt)
    return "\n".join(out)


def _flat(s):
    """比对口径：去掉所有空白。排版换行不是"内容差异"。"""
    return re.sub(r"\s+", "", s or "")


def _source_body(s):
    """把源稿归一成"成品里应当出现的那段正文"（2026-11-04，试跑实测假红）。

    首跑报 0/12 章一致，而解包 docx 复查成品完整（2719 段 / 57741 字符 /
    12 个章标题）—— 假红的根因是"拿未归一化的源去比已归一化的成品"：

    ① 源稿末尾带 `<!-- quality: … -->` / `<!-- summary: … -->` 注释，
       合并时被剥掉，不先去掉就永远比不中；
    ② 源稿正文里的半角 `"` 在合并阶段被归一成中文引号；
    ③ md 章标题的 `## ` 前缀在成品里不存在。

    三处都不是内容差异。这里**复用合并阶段那一个函数**（`text_norm.
    normalize_chinese_punctuation`，stage7 第 78 行调的就是它）而不是再写一份
    引号配对 —— 否则"合并用的口径"和"核对照的口径"又会分成两份，下次各自漂移。
    """
    s = re.sub(r"<!--.*?-->", "", s or "", flags=re.S)
    s = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", s, flags=re.M)
    from utils.text_norm import normalize_chinese_punctuation
    return normalize_chinese_punctuation(s)


def check_export_matches_source(docx_path, chapters, limit=8):
    """逐章核对：每一章的正文（去空白后）必须**原样**出现在成品里。

    `chapters` = {章号: 该章源文本}（调用方从 raw/refined 读）。
    返回 {"total", "matched", "mismatched":[{n, chars, why}], "ok"}。

    为什么按"整章文本是否原样出现"判，而不是逐字 diff：docx 会把段落拆开、
    加上分页/目录，肉眼逐字 diff 既不可信也不可读；"整章正文一字不差地在成品里"
    才是真正要保证的那件事（缺段 / 串章 / 截断都会打破它）。
    """
    doc = _flat(docx_plain_text(docx_path))
    items = []
    for n in sorted((chapters or {}), key=lambda x: int(x)):
        src = _flat(_source_body(chapters[n]))
        if not src:
            continue
        if src in doc:
            items.append({"n": int(n), "ok": True, "chars": len(src)})
        else:
            why = "成品里找不到这一章的完整正文（缺段 / 串章 / 排版改写）"
            # 开头在、后面没有 = 截断（这是最常见的残次形态），单独点名。
            # head 取 20 字：太长了短章会整段落空，反而认不出截断。
            head = src[:20]
            if head and head in doc:
                why = "成品里这一章被截断了（开头在，后文缺）"
            items.append({"n": int(n), "ok": False, "chars": len(src), "why": why})
    bad = [it for it in items if not it["ok"]]
    return {"total": len(items), "matched": len(items) - len(bad),
            "mismatched": bad, "ok": not bad,
            "docx_chars": len(doc), "source_chars": sum(it["chars"] for it in items)}


def check_docx_output(docx_path, md_text=None):
    """成品 docx 的导出后校验。返回 (errors, warnings)。

    过不了就删文件（调用方负责删；本函数只报）——“校验不过不出文件”的
    最后一环：占位符替换失败 / 章标题丢失的成品绝不能留在 output/ 里。
    """
    errors, warnings = [], []
    p = Path(docx_path)
    if not p.exists() or p.stat().st_size == 0:
        return ["成品 docx 不存在或为空"], warnings
    try:
        doc_xml, _ = _read_docx_xml(p)
    except Exception as e:                                    # noqa: BLE001
        return [f"成品 docx 不可打开：{type(e).__name__}: {e}"], warnings
    flat = re.sub(r"<[^>]+>", "", doc_xml)
    for ph in TEMPLATE_PLACEHOLDERS:
        if ph in flat:
            errors.append(f"成品残留占位符：{ph}（模板替换失败）")
    if md_text is not None:
        want = len(CHAPTER_NUM_RE.findall(md_text))
        # 只数 pStyle（段落样式）—— 裸 `w:val="1"` 会撞上 keepNext 等属性
        got = len(re.findall(r'<w:pStyle w:val="(?:1|Heading1|heading1)"', doc_xml))
        # 成品章标题按 Heading 1 计数（md 的 ## → 模板模式 Heading 1）
        if want and got < want:
            errors.append(f"成品章标题数不足：docx {got} 个 Heading 1 < "
                          f"md {want} 个章标题（结构错位）")
    return errors, warnings
