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
- chapter_review → data/outline/review_report.json（偶数章各 1 条发现）+ 回放 FILE 协议块
- stage5_proofread → 回放裸 JSON 到 stdout（每章 1 条 warn；proofread.py 从 stdout 解析）
- 其余（book_summary 等）→ 任务文本里「写入」目标写一行占位

仅用于 NF_API_ALLOW_FAKE=1 的测试模式与离线集成测试，不进真实流水线。
"""
import json
import re
from pathlib import Path

from utils.file_io import read_text, write_text

NUM = "[0-9]+"
NUM2 = "[0-9]{2}"   # 任务文件名里的两位章号（stage4_ch01.md）


# 静态句库（94 句 / 2294 字）：**逐句独立撰写**，彼此不共享 12 字连续片段。
#
# 为什么必须是静态字面量而非模板拼装：
# 退化检测用「12-gram 重复率」判复读。模板拼装（如 12 主语 x 12 动作 x ...）
# 会让每个片段在一章里出现几十次 —— 即使组合本身不重复，字符级 12-gram
# 仍大量撞车（实测 60%~70%），正文会被判退化。要压到阈值以下，
# 句子之间就不能复用任何 12 字以上的连续片段。实测本句库重复率 0.0000。
_BODY_CORPUS = [
    "他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。",
    "她停下脚步，侧过脸朝窗外的天色看了一眼，随即收回视线。",
    "老者抬起头，浑浊的眼睛在灯下眯了眯，手指在桌沿上慢慢摩挲。",
    "少年侧过身让出半步路，肩上的包袱却没卸下来，像是随时要走。",
    "那人把杯子放下，杯底和木桌碰出很轻的一响，茶水晃了一圈。",
    "店主擦了擦手，从柜台底下摸出一本卷了边的册子，翻到折角那页。",
    "守卫握紧长枪，枪尖在门框上投下一道细长的影子，随灯苗轻轻晃。",
    "来客抖落肩上的雨珠，进门时鞋底在门槛上蹭了两下才迈进来。",
    "旅人解开斗篷的系带，露出里面磨得发白的衣襟和一柄短刀。",
    "伙计压低声音说了句什么，被外头的雨声盖住，听不真切。",
    "孩子拽住母亲的衣角，仰着脸问了一句，声音里带着困意。",
    "女人拢了拢鬓边散下来的碎发，把包袱往怀里又抱紧了些。",
    "桌上那盏灯的火苗跳了一下，屋里的影子跟着长长短短地变形。",
    "窗外传来车轮碾过石板的声响，由远及近，又慢慢淡下去。",
    "远处钟楼正好敲了七下，余音散在潮湿的空气里，久久不散。",
    "走廊尽头的门开了一道缝，透出昏黄的光，很快又合上了。",
    "鼻尖闻到空气里混着旧纸、潮气和一点烧过柴火的焦味。",
    "指尖在桌面上敲了三下，停顿片刻，又轻轻敲了两下。",
    "呼吸放缓下来，肩膀却仍绷着，像是下定了某个决心。",
    "视线扫过屋里每一个角落，最后落在靠窗那张空着的椅子上。",
    "他把信纸对折两次，收进贴身的袖袋，按了按封口处。",
    "她从怀里摸出一枚旧铜钱，边缘已经被磨得看不出字迹。",
    "地图在膝上缓缓展开，边角有两处折痕，颜色比别处深。",
    "册子上密密麻麻记着些数字，最后一行被划掉了，墨迹很重。",
    "茶早已凉透，杯壁上凝着一圈深褐色的渍，映着灯的火。",
    "炉子里只剩一层暗红的光，柴禾偶尔爆出一个极小的火星。",
    "两个人的影子投在发黄的墙面上，一个高些，一个瘦些。",
    "檐下的水珠一滴一滴往下落，节奏不急，像是数着什么。",
    "夜色从窗棂之间一寸一寸漫进来，把桌角染成半明半暗。",
    "云压得很低，远处山脊的轮廓被雾气糊成了一片灰。",
    "他把地图卷起来，用一根麻绳捆好，塞回背上的行囊。",
    "她伸手拨了拨灯芯，火苗立时亮了些，屋里清楚了几分。",
    "门外传来轻轻的叩门声，三下，停顿，又是三下。",
    "屋里的空气静了片刻，只有雨水顺着屋檐往下淌的声响。",
    "谁也没有先开口，各自看着面前的茶盏，像是在等对方。",
    "他终究还是把杯子端起来，抿了一小口，又放回原处。",
    "她摇了摇头，起身走到窗边，把半开的窗扇往外推了推。",
    "影子在桌上拉长又缩短，随着灯火的晃动来回摇摆。",
    "墙角立着一只旧木箱，铜锁上积了灰，边角磕掉了一小块。",
    "他蹲下身，把箱盖掀开一条缝，往里看了一眼，又合上。",
    "楼梯传来脚步声，停在了门外，几息之后又慢慢走远了。",
    "她把斗篷重新系好，走到门口时回头看了一眼那张桌子。",
    "雨水顺着伞骨汇成细流，滴滴答答落在他脚边的青石上。",
    "店里只剩下最后一位客人，趴在角落的桌上睡着了。",
    "他数了数桌面上的铜板，勉强够再添一壶粗茶。",
    "伙计把门板一块一块装上，最后一块留了道缝没有合严。",
    "远处传来更夫的梆子声，两长一短，说明已经过了子时。",
    "她将灯端起来，光晕随之移动，照见墙上挂着的一幅旧画。",
    "画上是一片模糊的山水，落款处被人用墨涂掉了。",
    "他用指尖碰了碰画框，指尖沾上一层薄薄的灰。",
    "院里的老槐树被风吹得簌簌作响，落了几片叶子在石阶上。",
    "她弯腰拾起其中一片，对着灯看了看叶脉，又轻轻放开。",
    "叶片打着旋落在水洼里，很快就被泡软、沉了下去。",
    "他忽然记起某件旧事，眉头皱起，随即又舒展开来。",
    "她把灯搁回桌面，光晕重新稳住在桌子的正中央。",
    "门外雨势小了些，成了绵密的细丝，斜斜地飘着。",
    "巷子尽头的灯笼在风里晃，红光被雨水晕开成一片。",
    "他站起身，把椅子推回桌下，动作很轻，几乎没有声响。",
    "她跟着站起来，拢了拢衣襟，把包袱重新挎到肩上。",
    "两人一前一后走出店门，脚步踩在湿漉漉的石板上。",
    "身后的门被人从里面合上，屋里的光被截断在门缝处。",
    "雨后的街面泛着微光，倒映着两侧店铺残存的灯火。",
    "他们沿着巷子往北走，两边的屋檐滴着水，敲打伞面。",
    "转过一个弯，前面出现一座石桥，桥头蹲着两只石兽。",
    "桥下的水涨了些，浮着些枯枝和碎叶，缓缓流过去。",
    "他停在桥中央，扶着石栏往下看了一会儿，没有说话。",
    "她也停下来，站在他侧后方半步的位置，安静地等着。",
    "对岸有几户人家还亮着灯，窗纸透出暖黄的一小片。",
    "更远处是一道城墙的轮廓，在夜色里显得格外厚实。",
    "他收回视线，把伞往她那边偏了偏，继续往前走。",
    "城门口的值夜兵卒裹着蓑衣，靠墙站着打盹。",
    "他们从兵卒面前经过，对方掀起眼皮看了一眼，没有拦。",
    "城内主街上的铺子都上了板，只有一家茶棚还亮着。",
    "茶棚里坐着三三两两的人，说话的声压得很低。",
    "他们找了个靠里的位子坐下，要了两碗热汤。",
    "汤面上浮着几点油花，热气扑在脸上，驱散了些寒意。",
    "隔壁桌的人在低声议论什么，提到一个地名和一个姓氏。",
    "她端着碗的手顿了一下，随即若无其事地继续喝汤。",
    "他装作没听见，用筷子拨了拨碗里的葱花。",
    "过了一会儿，隔壁桌的人起身走了，留下几枚铜板在桌上。",
    "棚主过来收拾碗筷，顺手把桌上的铜板收进围裙口袋。",
    "雨已经停了，云层裂开一道缝，露出一点惨白的天光。",
    "他把最后一口汤喝完，放下碗，长长地呼出一口气。",
    "她掏出帕子擦了擦嘴角，把帕子叠好收回袖中。",
    "两人起身离座，帘子被掀开时带进一股清冷的风。",
    "外面的石板上积着浅浅的水，映出他们并行的影子。",
    "远处传来第一声鸡鸣，天边那线白正在慢慢扩大。",
    "他放慢了脚步，她也跟着慢下来，谁都没有提去哪。",
    "巷子转角处立着一根木桩，上面钉着一块褪色的告示。",
    "他走近看了两眼，告示上的字迹已经被雨水泡得模糊。",
    "她站在他身后，借着微光辨认了一会儿，摇了摇头。",
    "两人继续往前，脚步声在空落的街巷里传得很远。",
    "天光渐亮，屋檐的轮廓一点点从灰蓝里浮出来。",
    "一夜就这么过去了，什么都没发生，又像是什么都变了。",
]


def _words_para(min_words):
    """生成达标字数、**结构健康**的中文正文（多段、低重复）。

    历史坑（2026-09-20 修）：旧实现是 `sentence * N` —— 同一句重复、
    且**整章一坨无换行**。旧判据只管字数，所以一直放行；补上退化检测后
    立刻被抓（复读率 97% + 单段占 98%），连带打红 `test_failure_paths` F5。

    根因不是判据误报，而是**夹具在模拟非法产物**：所有用 FakeClient 跑
    stage4 的端到端用例，此前实际验证的是「垃圾正文能被放行」。
    这里改为产出多段、低重复的正文，让夹具忠于「合法章节」这一前提。

    实现：`_BODY_CORPUS` 静态句库逐句取用，每 2 句成段。
    超出句库容量（本章 >2294 字）时循环复用，重复率会回升 ——
    但默认 target_words 上限 3000 字，仍在可控范围（实测约 2%）。
    """
    corpus = _BODY_CORPUS
    need = max(int(min_words), 400)
    out, total, i = [], 0, 0
    while total < need:
        s = corpus[i % len(corpus)]
        out.append(s)
        total += len(s)
        i += 1
    # 每 2 句成一段：产生真实段落结构（段数 >= MIN_PARAGRAPHS）
    paras = ["".join(out[j:j + 2]) for j in range(0, len(out), 2)]
    return "\n\n".join(paras)

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
    nm = re.search("第(" + NUM + ") 章", name) or re.search("ch(" + NUM2 + ")", name)
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


def _write_review_report(text):
    """chapter_review：产出可被 chapter_review.py 解析的审查报告 JSON。

    fake 语义：偶数章各 1 条 warn 发现、奇数章无问题（覆盖"有问题/无问题"两条 GUI 分支），
    报告路径优先取任务里的「写入: xxx.json」，否则回退 data/outline/review_report.json。
    返回 (落盘路径列表, 供 stdout_tail 回放的 FILE 协议文本)——chapter_review 从
    stdout 解析 JSON，故必须回放协议块，只落盘不返回会导致解析失败。
    """
    m = re.search("写入[:：]\\s*([^\\r\\n]+?[.]json)", text)
    p = Path(m.group(1).strip()) if m else Path("data/outline/review_report.json")
    chapters = []
    for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
        dd = Path(d)
        if not dd.is_dir():
            continue
        for f in sorted(dd.glob("*.md")):
            try:
                n = int(f.stem)
            except ValueError:
                continue
            if any(c["n"] == n for c in chapters):
                continue
            findings = []
            if n % 2 == 0:
                findings = [{
                    "id": "f001",
                    "type": "outline_gap",
                    "severity": "warn",
                    "detail": "（fake）第 %d 章疑似遗漏本章大纲的关键事件" % n,
                    "suggested_action": "补写关键事件，保持与大纲一致",
                }]
            chapters.append({"n": n, "findings": findings})
        if chapters:
            break
    chapters.sort(key=lambda c: c["n"])
    payload = json.dumps({"chapters": chapters, "note": "（fake）测试用审查报告"},
                         ensure_ascii=False, indent=2)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, payload)
    return [p], "===FILE: " + str(p) + "===" + chr(10) + payload + chr(10) + "===END==="


def _write_proofread_findings(text):
    """stage5_proofread：产出可被 proofread.py 解析的 LLM 校对 JSON。

    fake 语义：从任务里数出出现的章号，每章给 1 条 warn（覆盖"有发现"分支），
    并回放裸 JSON 到 stdout（proofread 从 stdout 解析，不读文件）。
    """
    nos = sorted({int(x) for x in re.findall(r"### 第\s*(\d+)\s*章", text)})
    if not nos:
        nos = [1]
    findings = [{
        "chapter": n,
        "type": "fact",
        "severity": "warn",
        "detail": "（fake）第 %d 章存在与设定集不符的称谓用法" % n,
        "location": "（fake）原文片段",
        "suggestion": "（fake）统一为该角色的正式名",
    } for n in nos]
    payload = json.dumps({"findings": findings}, ensure_ascii=False, indent=2)
    return [], payload


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
        stdout_override = None
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
        elif name.startswith("chapter_review"):
            written, stdout_override = _write_review_report(text)
        elif name.startswith("batch_refine"):
            written = _refine_chapter(text)
        elif name.startswith("stage5_proofread"):
            written, stdout_override = _write_proofread_findings(text)
        elif name.startswith("stage5"):
            written = _copy_raw_to_checked(text)
        elif name.startswith("stage6"):
            written = _copy_checked_to_refined(text)
        else:
            written = _write_generic(text)
        est_in = max(1000, len(text) * 2 // 5)
        return {
            "exit_code": 0,
            "stdout_tail": stdout_override or ("（fake）已写出: " + ", ".join(str(x) for x in written)),
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
        elif name.startswith("chapter_review"):
            written, _ = _write_review_report(text)
        elif name.startswith("batch_refine"):
            written = _refine_chapter(text)
        elif name.startswith("stage5_proofread"):
            written, _ = _write_proofread_findings(text)
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
