# -*- coding: utf-8 -*-
"""同质化与退化检测（P1：零 token、纯确定性、只报不拦）。

## 为什么是代码不是提示词

2026-10 的四篇研究把话说死了：
- PNAS 2025《Echoes in AI》：同一 prompt 生成 100 次情节高度回声
  （GPT-4 续写卡夫卡，50/100 让警察指"第二个左转"）；
- Open Research Europe 2025：11,800 篇 / 236 国的故事收敛到同一结构；
- arXiv 2606.17350：**negative prompting 与 temperature 调节都无效**；
- arXiv 2603.14430：中文网文同质化最重，LLM 不理解叙事功能，只走僵化范式。

结论：提示词层面对抗同质化已被证否。能做的是**测出来**，交给人改。
所以本模块只输出指标与告警，不改稿、不阻断、不消耗 token。

## 五个指标（阈值均由构造样本校准，见 tests/unit/test_homogeneity.py）

| code | 算法 | 阈值 | 报什么 |
|---|---|---|---|
| `shingle_repeat` | 去标点后汉字 4-gram 重复覆盖率 | >0.45 | 复读、原地打转 |
| `phrase_repeat` | 5-15 字短语（去首尾虚词）重复覆盖率 | >0.20 | 同一组句子换个说法反复出现 |
| `dialogue_same_voice` | 角色对话的句长直方图 + 语气词分布，两两 JS 距离均值 | <0.15 | 所有人一个腔调 |
| `sentence_monotony` | 句长的标准差 / 均值（CV） | <0.45 | 句式单调，通篇一样长 |
| `metaphor_density` | 比喻词每千字出现数 | >8.0 | 比喻堆砌 |
| `sensory_lock` | 五感词 top-10 覆盖全部感官命中的比例 | >0.55 | 只用同几个感官词 |

**样本下限 600 字**（与 `verify_chapter.MIN_RATIO_BODY_CHARS` 同源）：
短样本上比率型指标没有意义，宁可跳过也不误报。

## 纪律

- **只报不拦**：不写回章节、不影响 exit code（与 text_norm 同一条原则：
  清洁工作坏了不该拖垮写作）。
- **可解释**：每条 finding 带 value / threshold，报告随章落盘，事后能复查。
- 误报的成本是"用户多看一眼"，漏报的成本是"烂稿静默通过"——所以阈值偏宽。
"""
import json
import math
import re
import sys
from pathlib import Path

# 允许 `python scripts/utils/homogeneity.py <file>` 直接跑（sys.path[0] 是 utils/）。
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# 分句与句长统计**复用 style_analyzer**（单源）：两套分句在标点边界上迟早漂移，
# 而它们要服务同一件事——判断这段文字"像不像人写的"。
from utils.style_analyzer import (                                     # noqa: E402
    _split_sentences as _style_split_sentences,
    _sentence_stats,
    prose_char_count,
)

# 与 verify_chapter.MIN_RATIO_BODY_CHARS 同值（用例 test_homogeneity 锁死两者相等，
# 防一边改了另一边没改 → 同一份稿子在两个闸门下"样本够/不够"自相矛盾）。
MIN_BODY_CHARS = 600

# ---- 预处理 --------------------------------------------------------------

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_TITLE_RE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.M)
_CJK_RE = re.compile(r"[^\u4e00-\u9fff0-9]")
_DIALOG_RE = re.compile(r"[“\"]([^”\"]{2,300})[”\"]")
_PHRASE_SPLIT_RE = re.compile(r"[。！？!?…，,；;：:、\n“”\"「」『』（）()\s]+")

# 短语首尾的虚词：切成短语后前后削掉，避免"他的""在门口"这类误计
_EDGE_STOP = "的了着过是在把被和与就都也还很更最不没这那有要会能可"

METAPHOR_WORDS = ("仿佛", "如同", "好似", "宛如", "似的", "像")
# "像"会被画像/雕像/图像/录像/偶像/人像误中，先替换掉再计
_FALSE_METAPHOR = ("画像", "雕像", "图像", "录像", "偶像", "人像", "像素", "不像话")

SENSORY_WORDS = {
    "sight": ("光", "暗", "亮", "影", "色", "白", "黑", "灰", "闪", "照"),
    "sound": ("声", "响", "静", "哑", "吵", "喊", "叫", "嗡", "轰", "寂"),
    "touch": ("冷", "热", "凉", "硬", "软", "湿", "干", "疼", "烫", "粗"),
    "smell": ("味", "香", "腥", "臭", "霉", "烟", "酸", "腥气", "气", "灰味"),
    "taste": ("甜", "苦", "涩", "咸", "辣", "淡", "腻", "鲜", "酸", "麻"),
}

MOOD_WORDS = ("吗", "呢", "吧", "啊", "呀", "哦", "嘛", "呗", "啦", "哟",
              "哈", "嗯", "唉", "哎", "嘿", "喂", "噢", "咧", "咯", "嘞")

# 阈值（构造样本校准：正常样本全部不报，退化样本逐项命中）
TH_SINGLE_REPEAT = 0.45
TH_PHRASE_REPEAT = 0.20
TH_VOICE_JSD = 0.15
TH_SENT_CV = 0.45
TH_METAPHOR_PER_K = 8.0
TH_SENSORY_TOP10 = 0.55


def body_text(text):
    """去掉注释块 / 代码围栏 / Markdown 标题，只留正文。"""
    text = _COMMENT_RE.sub("", text)
    text = _FENCE_RE.sub("", text)
    text = _TITLE_RE.sub("", text)
    return text.strip()


def cjk_only(s):
    return _CJK_RE.sub("", s)


def split_sentences(text):
    """分句走 style_analyzer 的实现，本模块不再维护第二套正则。"""
    return _style_split_sentences(text)


def sentence_lengths(text):
    return [len(s) for s in split_sentences(text)]


# ---- 指标 1/2：复读 ------------------------------------------------------

def shingle_repeat(text, n=4):
    """去标点后汉字 n-gram 的重复覆盖率。

    口径：**被重复覆盖的字符位置占比**（不是把重复 shingle 计数相加）。
    首版按 `(c-1)*(n-1)` 累加，重叠 shingle 会被平方级放大 —— 实测 644 字的
    极端样本算出 2.55（>1），等于判据本身说谎。现改为位置标记法，上限恒为 1。
    """
    flat = cjk_only(text)
    if len(flat) < n * 4:
        return 0.0, 0
    positions = {}
    for i in range(len(flat) - n + 1):
        positions.setdefault(flat[i:i + n], []).append(i)
    covered = [False] * len(flat)
    for occ in positions.values():
        if len(occ) < 2:
            continue
        for start in occ[1:]:                   # 第一次出现算"原句"，从第二次起算复读
            for k in range(n):
                covered[start + k] = True
    return round(sum(covered) / float(len(flat)), 4), len(positions)


def phrase_repeat(text):
    """5-15 字短语的重复覆盖率（去首尾虚词后仍 >=4 字才计）。"""
    phrases = []
    for raw in _PHRASE_SPLIT_RE.split(text):
        p = raw.strip()
        if len(cjk_only(p)) < 5 or len(p) > 15:
            continue
        p2 = p.strip(_EDGE_STOP)
        if len(cjk_only(p2)) < 4:
            continue
        phrases.append(p2)
    if len(phrases) < 6:
        return 0.0, len(phrases)
    counts = {}
    for p in phrases:
        counts[p] = counts.get(p, 0) + 1
    total = sum(len(p) for p in phrases)
    repeated = sum(len(p) * c for p, c in counts.items() if c > 1)
    return round(repeated / max(total, 1), 4), len(phrases)


# ---- 指标 3：对话同腔 ----------------------------------------------------

def _js_distance(p, q):
    """Jensen-Shannon 距离（0-1，对称）。"""
    eps = 1e-9
    p = [x + eps for x in p]
    q = [x + eps for x in q]
    m = [(a + b) / 2 for a, b in zip(p, q)]

    def kl(a, b):
        return sum(x * math.log(x / y, 2) for x, y in zip(a, b) if x > 0)

    return math.sqrt(max(0.0, 0.5 * kl(p, m) + 0.5 * kl(q, m)))


def _dialogue_features(segments):
    """一段角色的对话 → 特征向量（句长 5 箱 + 语气词 8 类）。"""
    lens = [1, 5, 10, 20, 40]                  # 分箱下界
    hist = [0] * (len(lens) + 1)
    mood = [0] * 8
    for s in segments:
        n = len(cjk_only(s))
        idx = 0
        for i, low in enumerate(lens):
            if n >= low:
                idx = i
        hist[min(idx, len(hist) - 1)] += 1
        tail = s.strip()[-2:]
        for j in range(8):
            if MOOD_WORDS[j] in tail:
                mood[j] += 1
                break
    total = len(segments)
    return [h / total for h in hist] + [m / total for m in mood]


def dialogue_same_voice(text, names):
    """按角色分组的对话特征两两 JS 距离均值。返回 (距离, 参与角色数)。"""
    names = [n for n in (names or []) if n]
    if len(names) < 2:
        return None, 0
    buckets = {n: [] for n in names}
    for m in _DIALOG_RE.finditer(text):
        seg = m.group(1).strip()
        head = text[max(0, m.start() - 40):m.start()]
        speaker, pos = None, -1
        for n in names:
            p = head.rfind(n)
            if p > pos:
                speaker, pos = n, p
        if speaker and len(cjk_only(seg)) >= 2:
            buckets[speaker].append(seg)
    usable = {n: v for n, v in buckets.items() if len(v) >= 3}
    if len(usable) < 2:
        return None, len(usable)
    keys = sorted(usable)
    feats = {k: _dialogue_features(usable[k]) for k in keys}
    dists = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            dists.append(_js_distance(feats[keys[i]], feats[keys[j]]))
    return round(sum(dists) / len(dists), 4), len(usable)


# ---- 指标 4/5：句式与比喻 ------------------------------------------------

def sentence_cv(text):
    """句长变异系数（std/mean）。口径与 style_analyzer._sentence_stats 完全一致 ——
    那边 cv<0.35 判「节奏均匀平稳」，这边取 0.45 作告警线（宁可漏报，不误报）。"""
    sentences = split_sentences(text)
    if len(sentences) < 20:
        return None, len(sentences)
    st = _sentence_stats(sentences)
    return st.get("cv"), st.get("count", len(sentences))


def metaphor_density(text):
    t = text
    for w in _FALSE_METAPHOR:
        t = t.replace(w, "＃")
    hits = sum(t.count(w) for w in METAPHOR_WORDS)
    chars = prose_char_count(text)
    return round(hits * 1000.0 / max(chars, 1), 2), hits


def sensory_lock(text):
    counts = {}
    total = 0
    for words in SENSORY_WORDS.values():
        for w in words:
            c = text.count(w)
            if c:
                counts[w] = c
                total += c
    if total < 40:
        return None, total
    top = sorted(counts.values(), reverse=True)[:10]
    return round(sum(top) / total, 4), total


# ---- 汇总 ----------------------------------------------------------------

def analyze(text, names=None, chapter=None):
    """返回 {"chapter","chars","enabled","metrics","findings","skipped"}。"""
    names = names or []
    body = body_text(text)
    chars = prose_char_count(body)
    out = {
        "chapter": chapter,
        "chars": chars,
        "enabled": chars >= MIN_BODY_CHARS,
        "metrics": {},
        "findings": [],
        "skipped": [],
    }
    if chars < MIN_BODY_CHARS:
        out["skipped"].append("正文 %d 字 < %d，比率型指标不计算" % (chars, MIN_BODY_CHARS))
        return out

    def add(code, value, threshold, detail, level="warn"):
        out["findings"].append({
            "code": code, "level": level, "value": value,
            "threshold": threshold, "detail": detail,
        })

    sr, ng = shingle_repeat(body)
    out["metrics"]["shingle_repeat"] = sr
    if sr > TH_SINGLE_REPEAT:
        add("shingle_repeat", sr, TH_SINGLE_REPEAT,
            "字符 4-gram 重复覆盖率 %.2f（%d 个 gram）：同一段话在原地打转" % (sr, ng))

    pr, npz = phrase_repeat(body)
    out["metrics"]["phrase_repeat"] = pr
    if pr > TH_PHRASE_REPEAT:
        add("phrase_repeat", pr, TH_PHRASE_REPEAT,
            "短语重复覆盖率 %.2f（%d 个短语）：同一组句子换个说法反复出现" % (pr, npz))

    jsd, speakers = dialogue_same_voice(body, names)
    if jsd is None:
        out["skipped"].append("dialogue_same_voice：可归属对话的角色仅 %d 个（需 >=2 且各 >=3 段）"
                              % speakers)
    else:
        out["metrics"]["dialogue_same_voice"] = jsd
        if jsd < TH_VOICE_JSD:
            add("dialogue_same_voice", jsd, TH_VOICE_JSD,
                "角色对话距离 %.3f（%d 个角色）：所有人一个腔调" % (jsd, speakers))

    cv, nsent = sentence_cv(body)
    if cv is None:
        out["skipped"].append("sentence_monotony：有效句数 %d < 20" % nsent)
    else:
        out["metrics"]["sentence_cv"] = cv
        if cv < TH_SENT_CV:
            add("sentence_monotony", cv, TH_SENT_CV,
                "句长变异系数 %.2f（%d 句）：通篇句式一样长" % (cv, nsent))

    md, hits = metaphor_density(body)
    out["metrics"]["metaphor_per_k"] = md
    if md > TH_METAPHOR_PER_K:
        add("metaphor_density", md, TH_METAPHOR_PER_K,
            "比喻 %d 次 / 每千字 %.1f：比喻堆砌" % (hits, md))

    sl, shits = sensory_lock(body)
    if sl is None:
        out["skipped"].append("sensory_lock：五感词命中 %d < 40" % shits)
    else:
        out["metrics"]["sensory_top10"] = sl
        if sl > TH_SENSORY_TOP10:
            add("sensory_lock", sl, TH_SENSORY_TOP10,
                "top-10 感官词覆盖 %.2f（共 %d 次命中）：只用同几个感官词" % (sl, shits))

    return out


def load_character_names(setting_path):
    """从设定集读角色名；读不到返回空列表（对话同腔指标会自动跳过）。"""
    p = Path(setting_path) if setting_path else None
    if not p or not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return []
    names = []
    for c in data.get("characters") or []:
        n = (c or {}).get("name")
        if n:
            names.append(str(n))
    return names


def write_chapter_report(chapter_path, chapter_no=None, setting_path=None,
                         out_dir=None):
    """写 data/state/homogeneity/<章节名>.json，并返回结果 dict。

    不抛异常给调用方 —— 调用方只负责 try/except 打印（与 text_norm 同纪律）。
    """
    p = Path(chapter_path)
    text = p.read_text(encoding="utf-8", errors="replace")
    res = analyze(text, load_character_names(setting_path), chapter_no)
    if out_dir is None:
        out_dir = p.resolve().parents[2] / "data" / "state" / "homogeneity"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / (p.stem + ".json")).write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def summarize(res):
    """一行摘要（给日志用）。"""
    if not res.get("enabled"):
        return "样本不足（%d 字），跳过" % res.get("chars", 0)
    if not res["findings"]:
        return "%d 字，无异常" % res["chars"]
    codes = "、".join(f["code"] for f in res["findings"])
    return "%d 字，%d 项待看（%s）" % (res["chars"], len(res["findings"]), codes)


def report_to_console(path, chapter_no=None, setting_path=None, tag="stage4"):
    """写报告 + 打印摘要。**只报不拦**，异常也不抛给调用方。

    单源入口：stage4（初稿）与 stage6（润色稿）都调这里，避免两份 try/except
    各写一遍 —— 那种重复最后一定分叉（一处加了打印、另一处没加）。
    """
    try:
        res = write_chapter_report(path, chapter_no, setting_path)
        print("[%s] 第%s章同质化检测：%s" % (tag, chapter_no, summarize(res)))
        for f in res["findings"]:
            print("[%s]   待看 %s：%s" % (tag, f["code"], f["detail"]))
        return res
    except Exception as e:                                     # noqa: BLE001
        print("[homogeneity] 第%s章检测失败（不影响主流程）: %s: %s"
              % (chapter_no, type(e).__name__, e))
        return None


# ---- 聚合读取（GUI / nfctl 共用同一份口径）-------------------------------

def reports_dir(root=None):
    base = Path(root) if root else Path(".").resolve()
    return base / "data" / "state" / "homogeneity"


def collect_reports(root=None):
    """汇总全部章节报告（只读）。

    刻意**不重算**：重算会把"落盘那一刻"的结论换成"现在这版正文"的结论，
    而诊断的价值就在于它记的是当时那一版。
    """
    d = reports_dir(root)
    items = []
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    for p in files:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            items.append({"chapter": p.stem, "error": "解析失败"})
            continue
        items.append({
            "chapter": p.stem,
            "chars": j.get("chars", 0),
            "enabled": bool(j.get("enabled")),
            "metrics": j.get("metrics") or {},
            "findings": j.get("findings") or [],
            "skipped": j.get("skipped") or [],
        })
    return {
        "dir": str(d),
        "chapters": items,
        "findings": sum(len(i.get("findings") or []) for i in items),
    }


def load_report(root, chapter):
    """取单章完整报告。

    三级匹配：① 文件名与章号一致（`01.json`）② 去掉前导零后一致（1 ↔ 01）
    ③ 报告里记的 `chapter` 字段一致（文件名不规范时仍能找到）。
    """
    d = reports_dir(root)
    key = str(chapter).strip()
    want = key.lstrip("0") or "0"
    p = d / (key + ".json")
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    for c in files:
        if (c.stem.lstrip("0") or "0") == want:
            return json.loads(c.read_text(encoding="utf-8"))
    for c in files:
        try:
            j = json.loads(c.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
        if str(j.get("chapter")) == want:
            return j
    return None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="同质化/退化检测（零 token，只报不拦）")
    ap.add_argument("file", help="章节 md 文件")
    ap.add_argument("--names", default="", help="角色名，逗号分隔（对话同腔用）")
    ap.add_argument("--setting", default="", help="设定集 json（自动取角色名）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--out", default="", help="把报告写到指定目录")
    args = ap.parse_args(argv)

    p = Path(args.file)
    if not p.exists():
        print("[homogeneity] 文件不存在: %s" % p)
        return 1
    names = [x.strip() for x in args.names.split(",") if x.strip()]
    if not names and args.setting:
        names = load_character_names(args.setting)
    res = analyze(p.read_text(encoding="utf-8", errors="replace"), names)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / (p.stem + ".json")).write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("[homogeneity] %s：%s" % (p.name, summarize(res)))
        for f in res["findings"]:
            print("  - [%s] %s" % (f["code"], f["detail"]))
        for s in res["skipped"]:
            print("  - (跳过) %s" % s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
