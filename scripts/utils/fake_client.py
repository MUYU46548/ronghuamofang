# -*- coding: utf-8 -*-
"""FakeClient：无 LLM 的全链路假执行器（P0-3 先例复活，P0 API 验收专用）。

按任务文件名推断阶段，写出能通过各阶段确定性校验的产物：
- stage1_scraps → data/setting/scraps_merge.json（points/conflicts/open_questions）
- stage1 → data/setting/setting.json（四顶层键 + 至少一个角色）
- stage2/stage2_refine → data/outline/global.md（起承转合/关键节点/预计章节数）
- stage3 → data/outline/chapters/NN.md（核心事件/涉及角色/功能）
- stage4 → data/chapters/raw/NN.md（标题+正文达标字数+quality+summary 注释）
- stage5 → data/chapters/checked/（raw 全量复制）
- stage6 → data/chapters/refined/（checked 复制，字数不变）
- batch_refine → 就地改写目标章节（保留字数，quality 置 8/10），供 auto_rewrite 链路测试
- 其余（book_summary 等）→ 任务文本里「写入」目标写一行占位

仅用于 NF_API_ALLOW_FAKE=1 的测试模式与离线集成测试，不进真实流水线。
"""
import json
import re
from pathlib import Path

from utils.file_io import read_text, write_text

NUM = "[0-9]+"


def _words_para(min_words):
    """生成达标字数的中文正文（每句 24 字，超 min 即停）。"""
    sentence = "他推门进来，把伞放在门边，看了一眼窗外的雨，然后点起了灯。"
    need = max(int(min_words), 400)
    body = sentence * (need // len(sentence) + 2)
    return body


def _write_scraps_merge(text):
    """阶段1a：碎片提炼产物（points/conflicts/open_questions）。"""
    m = re.search("写入文件:\\s*([^ \\r\\n]+[.]json)", text)
    p = Path(m.group(1)) if m else Path("data/setting/scraps_merge.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps({
        "points": [{
            "id": "p001", "cluster": "fake簇", "cluster_note": "",
            "ts": None, "ts_source": "mtime",
            "content": "（fake）碎片提炼出的一个信息点",
            "suggest_card": "角色卡", "confidence": "low",
            "source_file": "fake.md",
        }],
        "conflicts": [],
        "open_questions": ["（fake）留待作者确定的事项"],
    }, ensure_ascii=False, indent=2))
    return [p]


def _write_setting(_text):
    p = Path("data/setting/setting.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, (
        '{"characters":[{"id":"c1","name":"测试角色","role":"主角",'
        '"traits":["冷静"],"relations":[],"source":"fake"}],'
        '"world":{"locations":[],"factions":[],"magic_system":[],"items":[]},'
        '"plot_fragments":[],"timeline":[],'
        '"_meta":{"version":1,"generated_from":["fake"],"vault_readonly":true}}'))
    return [p]


def _write_global(text):
    # 优先遵循任务里的「写入文件: xxx.md」（与 _write_chapter 一致），
    # 否则退回默认 data/outline/global.md —— 多方案 draft 生成依赖此约定。
    m = re.search("写入文件:\\s*([^ \\r\\n]+[.]md)", text)
    p = Path(m.group(1)) if m else Path("data/outline/global.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, (
        "# 测试书 整体大纲\n\n"
        "## 起\n开篇：主角登场，核心冲突埋设。\n\n"
        "## 承\n发展：矛盾升级，线索铺开。\n\n"
        "## 转\n转折：关键事件爆发。\n\n"
        "## 合\n收束：主线闭合，余韵留白。\n\n"
        "## 关键节点\n- 节点1：起承转合各占一拍\n\n"
        "## 预计章节数\n\n3\n"))
    return [p]


def _write_chapter_outlines(text):
    nums = re.findall("第(" + NUM + ")章", text)
    out = []
    for n in sorted({int(x) for x in nums}):
        p = Path("data/outline/chapters") / (("%02d" % n) + ".md")
        p.parent.mkdir(parents=True, exist_ok=True)
        write_text(p, ("# 第" + str(n) + "章 大纲（fake）\n\n"
                       "- 核心事件：测试事件推进\n"
                       "- 涉及角色：测试角色（主角）\n"
                       "- 功能：承接上章，推进主线\n"))
        out.append(p)
    return out


def _write_chapter(text, name):
    m = re.search("写入文件: ([^ \\n]+[.]md)", text)
    out_path = Path(m.group(1)) if m else Path("data/chapters/raw") / name
    mm = re.search("字数 (" + NUM + ")-(" + NUM + ") 字", text)
    min_w = int(mm.group(1)) if mm else 300
    chap = ""
    nm = re.search("第(" + NUM + ") 章", name) or re.search("ch(" + NUM + "{2})", name)
    chap = nm.group(1) if nm else "00"
    p = out_path
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, ("## 第" + str(int(chap)) + "章 测试章节\n\n"
                   + _words_para(min_w) + "\n\n"
                   "<!-- quality: 8/10 -->\n"
                   "<!-- summary: 测试摘要：主角推进主线，无新增设定。 -->\n"))
    return [p]


def _refine_chapter(text):
    """batch_refine / auto_rewrite：按任务里的目标路径就地改写章节。

    fake 语义：保留章节正文与字数（满足 ±20% 铁律），仅把 quality 注释置为 8/10，
    使「重写 → 复评 → 幂等」链路可端到端验证。
    """
    m = re.search("写入[:：]\\s*([^ \\n]+[.]md)", text)
    if not m:
        m = re.search("写入文件[:：]\\s*([^ \\n]+[.]md)", text)
    if not m:
        return _write_generic(text)
    p = Path(m.group(1))
    if not p.exists():
        return _write_generic(text)
    body = read_text(p)
    new_body, n = re.subn(r"<!--\s*quality:\s*[\d.]+(?:\s*/\s*10)?\s*-->",
                          "<!-- quality: 8/10 -->", body)
    if n == 0:
        new_body = body.rstrip() + "\n\n<!-- quality: 8/10 -->\n"
    write_text(p, new_body)
    return [p]


def _copy_raw_to_checked(_text):
    src, out = [], []
    raw = Path("data/chapters/raw")
    if raw.exists():
        for f in sorted(raw.glob("*.md")):
            dst = Path("data/chapters/checked") / f.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            write_text(dst, read_text(f))
            src.append(f)
            out.append(dst)
    report = Path("data/outline/check_report.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    if not report.exists():
        write_text(report, "# 逻辑检查报告\n\n## 问题清单\n-（fake：无问题）\n")
    return out


def _copy_checked_to_refined(text):
    out = []
    m = re.search("全文写入: ([^ \\n]+)/NN[.]md", text)
    if m:
        refined_dir = Path(m.group(1))
    else:
        refined_dir = Path("data/chapters/refined")
    refined_dir.mkdir(parents=True, exist_ok=True)
    checked = Path("data/chapters/checked")
    for f in sorted(checked.glob("*.md")):
        dst = refined_dir / f.name
        write_text(dst, read_text(f))
        out.append(dst)
    report = Path("data/outline/polish_report.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    if not report.exists():
        write_text(report, "# 润色修改清单\n\n无修改\n")
    return out


def _write_generic(text):
    out = []
    for m in re.finditer("写入文件: ([^ \\n]+[.]md)", text):
        p = Path(m.group(1))
        p.parent.mkdir(parents=True, exist_ok=True)
        write_text(p, "（fake 产物）\n")
        out.append(p)
    if not out:
        m2 = re.search("写入: ([^ \\n]+[.]md)", text)
        if m2:
            p = Path(m2.group(1))
            p.parent.mkdir(parents=True, exist_ok=True)
            write_text(p, "（fake 产物）\n")
            out.append(p)
    return out


class FakeClient:
    """与 HermesClient/OpenAICompatClient 同签名 run_task/write_task。"""

    provider = "fake"
    model = "fake"

    def write_task(self, task_dir, name, content):
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        write_text(path, content)
        return path

    def run_task(self, task_file, workdir=None, model=None):
        path = Path(task_file)
        text = read_text(path)
        name = path.name
        if name.startswith("stage1_scraps"):
            written = _write_scraps_merge(text)
        elif name.startswith("stage1"):
            written = _write_setting(text)
        elif name.startswith("stage2"):
            written = _write_global(text)
        elif name.startswith("stage3"):
            written = _write_chapter_outlines(text)
        elif name.startswith("stage4_ch"):
            written = _write_chapter(text, name)
        elif name.startswith("batch_refine"):
            written = _refine_chapter(text)
        elif name.startswith("stage5"):
            written = _copy_raw_to_checked(text)
        elif name.startswith("stage6"):
            written = _copy_checked_to_refined(text)
        else:
            written = _write_generic(text)
        est_in = max(1000, len(text) * 2 // 5)
        return {
            "exit_code": 0,
            "stdout_tail": "（fake）已写出: " + ", ".join(str(x) for x in written),
            "tokens": est_in,
            "tokens_out": max(500, est_in // 3),
            "cost_yuan": 0.0,
            "estimated": True,
            "provider": "fake",
            "model": "fake",
            "requests": 1,
        }

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None, model=None):
        """Fake 流式：模拟逐字符输出。"""
        import time
        path = Path(task_file)
        text = read_text(path)
        name = path.name
        if name.startswith("stage1_scraps"):
            written = _write_scraps_merge(text)
        elif name.startswith("stage1"):
            written = _write_setting(text)
        elif name.startswith("stage2"):
            written = _write_global(text)
        elif name.startswith("stage3"):
            written = _write_chapter_outlines(text)
        elif name.startswith("stage4_ch"):
            written = _write_chapter(text, name)
        elif name.startswith("batch_refine"):
            written = _refine_chapter(text)
        elif name.startswith("stage5"):
            written = _copy_raw_to_checked(text)
        elif name.startswith("stage6"):
            written = _copy_checked_to_refined(text)
        else:
            written = _write_generic(text)
        # 模拟流式输出
        msg = "（fake 流式）已写出: " + ", ".join(str(x) for x in written)
        for ch in msg:
            if stop_flag and stop_flag():
                break
            on_piece(ch)
            time.sleep(0.02)
        est_in = max(1000, len(text) * 2 // 5)
        return {
            "exit_code": 0,
            "stdout_tail": msg,
            "tokens": est_in,
            "tokens_out": max(500, est_in // 3),
            "cost_yuan": 0.0,
            "estimated": True,
            "provider": "fake",
            "model": "fake",
            "requests": 1,
            "stopped": bool(stop_flag and stop_flag()),
        }
