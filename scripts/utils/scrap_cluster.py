# -*- coding: utf-8 -*-
"""原始创作碎片聚类（确定性核心，零 LLM）。

背景与定位
----------
`materials/original_scraps/` 存放**用户自由命名**的原始碎片——没有统一命名格式，
只为用户自己记忆服务。因此本模块**不依赖文件名格式**：

- 时间戳：文件名全文 → 正文前若干行 → 文件 mtime，三级回退，并标注来源可信度；
- 聚类：**内容驱动**——中文 n-gram 共现词 + 文本相似度 + 主题词提示，用并查集合并；
  文件名里的语义片段只作**弱提示**（单独命中不足以成簇），因为自由命名不可靠；
- 前瞻备忘：识别"待定/后续/回头再说"类语句，供归并与写作阶段重点消化。

输出 `data/setting/scraps_index.json`：
聚类结果 + 每簇证据（可审计）+ 时间轴 + 前瞻备忘 + 内联分批建议。
其中 `content_fingerprint` 只对碎片**内容**做 sha256（不含 generated_at 等易变字段），
供 stage1 判定"素材是否有实质变化"，避免改碎片不触发重归并、或每次运行都误判为有变化。

用法：
  python scripts/utils/scrap_cluster.py                     # 生成索引并打印摘要
  python scripts/utils/scrap_cluster.py --json              # 机器可读
  python scripts/utils/scrap_cluster.py --dir <碎片目录> --out <索引路径>
  python scripts/utils/scrap_cluster.py --explain           # 打印每簇合并证据
"""
import argparse
import difflib
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_UTILS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _UTILS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils.file_io import read_text, write_text  # noqa: E402

DEFAULT_SCRAP_DIR = "materials/original_scraps"
DEFAULT_INDEX_OUT = "data/setting/scraps_index.json"

SCRAP_EXTS = {".md", ".markdown", ".txt"}

# 时间戳（自由命名 → 允许出现在文件名任意位置、也允许出现在正文前几行）
DATE_FULL_RE = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?")
DATE_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
# 纯数字命名："0312"（月日）、"20240305"（年月日）——自由命名里很常见。
# 只在**整个文件名**就是这串数字时生效，避免把"第0312条"这类正文当日期。
BARE_YMD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
BARE_MDD_RE = re.compile(r"^(\d{2})(\d{2})$")
CONTENT_TS_HEAD_LINES = 5      # 正文里找时间戳只看前 N 行（避免正文叙述里的年份误判）

# 前瞻备忘：写着"以后再说"的内容，归并时不能当既成事实
LOOKAHEAD_RE = re.compile(
    r"(待定|待确认|待补|存疑|不确定|未定|暂时|先这样|先放着|回头|以后再|后续再|之后再|"
    r"TODO|FIXME|\?\?+)",
    re.IGNORECASE)

# 中文虚词/助词字符：含有这些字符的 n-gram 一律不是"特征词"。
# 没有分词器时，n-gram 会大量跨越词边界（"个人沉默""的故事""了月兔却"），
# 它们凭长度压过真特征词，还会让两条无关笔记因为"都写了 X的故事"而误合并。
# 真正的中文实词极少包含 的/了/是/在/和 这类字，这条过滤比调阈值稳得多。
FUNC_CHARS = set(
    "的了是在和与也都没就不而或还又很太更最一个上下中里外前后时候这那"
    "有会要能可说但若如因由于当使得着过们他她它我你之其所以为及并且"
    "把被给让对从到将则相把就都也很把呗嘛呢吧啊么们"
)

# 负向提及线索：碎片里常有"这和月神无关"这类**排除性**注释（写给自己做区分用）。
# 若不处理，"月神"的文档频率会被抬到"人人都有"，反而把真正的月神簇拆散。
# 口径刻意收窄：只认明确的排除语义，"不是/没有" 这类歧义过大的词不列入。
NEGATION_CUE_RE = re.compile(r"(无关|没关系|没有关系|无关系|不属于|算不上|并非同一)")
NEGATION_WINDOW = 12           # 线索词前 N 个字符内找被否定的特征词

# 中文虚词与"泛用词"：作为聚类特征词没有区分度，且会让所有碎片连成一团
STOP_TOKENS = {
    # 虚词 / 连接词
    "这个", "那个", "然后", "但是", "因为", "所以", "如果", "还是", "就是", "不是",
    "没有", "一个", "一些", "自己", "他们", "我们", "你们", "之后", "之前", "时候",
    "东西", "事情", "感觉", "觉得", "好像", "似乎", "而且", "不过", "只是", "现在",
    "以后", "今天", "明天", "昨天", "可以", "可能", "应该", "什么", "怎么", "这里",
    "那里", "知道", "看到", "想到", "一下", "有点", "其实", "真的", "已经", "一直",
    "非常", "特别", "主要", "问题", "内容", "情况", "部分", "地方", "还有", "或者",
    "以及", "同时", "另外", "总之", "反正", "大概", "也许", "需要", "必须", "这样",
    "那样", "多少", "几个", "第一", "第二", "第三", "开始", "结束", "继续", "直接",
    # 本语料内的泛用词（无区分度，否则所有碎片都会连成一簇）
    "人物", "故事", "设定", "场景", "角色", "碎片", "笔记", "随手", "备忘", "记录",
    "备注", "草稿", "想法", "灵感", "大纲", "情节", "世界观", "时间线", "关系",
    "对话", "描写", "细节", "背景", "名字", "称呼", "台词", "剧情", "章节",
}

HINT_SEP_RE = re.compile(r"[_\-—–·．.、,，:：;；\s【】\[\]()（）{}<>《》\"'|/\\+~]+")
MD_NOISE_RE = re.compile(r"[#*`>_~\[\]()!|]+")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")

MIN_NGRAM, MAX_NGRAM = 2, 4
MIN_LATIN_LEN = 3
KEYWORDS_PER_SCRAP = 6
CLUSTER_KEYWORDS = 6

SIM_THRESHOLD = 0.55          # difflib 相似度：单独即可成簇（强证据）
MIN_SHARED_TOKENS = 2         # 共享特征词数量：≥2 即可成簇
SHARED_TOKEN_MIN_LEN = 3      # 单个共享特征词成簇的最低长度（长词更有区分度）
HINT_ONLY_SCORE = False       # 文件名提示单独命中**不足以**成簇（自由命名不可靠）
INLINE_CHAR_BUDGET = 180000   # 单批内联字符上限（llm_client 硬上限 220000，留安全余量）


@dataclass
class Scrap:
    """单个原始碎片。"""

    path: str
    name: str
    stem: str
    chars: int = 0
    lines: int = 0
    ts: str = None
    ts_source: str = "unknown"   # filename | content | mtime
    ts_partial: bool = False     # 缺年份（仅月日）
    topic_hint: str = ""
    keywords: list = field(default_factory=list)   # [(token, score)]
    lookahead: list = field(default_factory=list)  # [{line_no, sentence}]
    negated: list = field(default_factory=list)    # 被排除性语境剔除的特征词（透明化）
    excerpt: str = ""
    norm_text: str = ""          # 不进 JSON
    tokens: dict = field(default_factory=dict)     # 已剔除否定词的特征词表（不进 JSON）
    candidates: set = field(default_factory=set)   # 全部有区分度的词（仅用于成簇判定，不进 JSON）

    def to_json(self):
        return {
            "name": self.name,
            "path": self.path,
            "ts": self.ts,
            "ts_source": self.ts_source,
            "ts_partial": self.ts_partial,
            "topic_hint": self.topic_hint,
            "keywords": [t for t, _ in self.keywords],
            "negated": self.negated,
            "lookahead": self.lookahead,
            "chars": self.chars,
            "lines": self.lines,
            "excerpt": self.excerpt,
        }


@dataclass
class Cluster:
    """一个碎片簇。"""

    name: str
    kind: str = "single"          # merged（多碎片合并）| single（单例）
    members: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    evidence: list = field(default_factory=list)

    def to_json(self):
        ts_list = [s.ts for s in self.members if s.ts]
        return {
            "name": self.name,
            "kind": self.kind,
            "count": len(self.members),
            "ts_range": [min(ts_list), max(ts_list)] if ts_list else None,
            "keywords": self.keywords,
            "evidence": self.evidence,
            "lookaheads": [dict(la, file=s.name, cluster=self.name)
                           for s in self.members for la in s.lookahead],
            "scraps": [s.to_json() for s in self.members],
        }


# --------------------------------------------------------------------------
# 文本处理
# --------------------------------------------------------------------------

def normalize_text(text):
    """归一化：去 Markdown 噪声，标点/空白一律换成**分隔符**，小写。

    注意这里不能把标点直接删掉：删掉会让 n-gram 跨越句子边界，
    造出「同类待定」（原文是"却没给他们同类。待定：…"）这类垃圾特征词，
    还会污染簇名。换成空格后，CJK 连续片段会自然在标点处断开。
    """
    t = MD_NOISE_RE.sub(" ", text or "")
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t.lower()


def extract_tokens(text):
    """抽取候选特征词：中文 2-4 gram + 英文/数字词（≥3 字符）。

    含虚词字符的 n-gram 直接丢弃（见 FUNC_CHARS）——没有分词器时这是剔除
    "跨词边界垃圾 n-gram" 最稳的一道闸。
    """
    tokens = Counter()
    for run in CJK_RUN_RE.findall(text or ""):
        n = len(run)
        for size in range(MIN_NGRAM, MAX_NGRAM + 1):
            if n < size:
                continue
            for i in range(n - size + 1):
                g = run[i:i + size]
                if any(ch in FUNC_CHARS for ch in g):
                    continue
                tokens[g] += 1
    for w in LATIN_TOKEN_RE.findall(text or ""):
        if len(w) >= MIN_LATIN_LEN:
            tokens[w.lower()] += 1
    return tokens


def is_discriminative(token, df, total_docs):
    """特征词是否具备区分度。

    只剔除两类：停用词/纯数字，以及**每个碎片都出现**的通用词。
    刻意不做"出现率 >60% 就丢弃"的激进过滤——碎片语料很小（常常只有十几条），
    主角名这类真特征词本来就高频，激进过滤会把它们误杀，导致该合并的簇合不上。
    """
    if not token or token in STOP_TOKENS:
        return False
    if token.isdigit():
        return False
    if total_docs >= 3 and df >= total_docs:
        return False   # 每个碎片都有 → 没有区分度
    return True


def extract_lookaheads(text, limit=3):
    """识别前瞻备忘语句（跳过代码围栏与引用行）。"""
    out = []
    for i, line in enumerate((text or "").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith(">") or s.startswith("```") or s.startswith("|"):
            continue
        m = LOOKAHEAD_RE.search(s)
        if not m:
            continue
        sentence = s[:120] + ("…" if len(s) > 120 else "")
        out.append({"line_no": i, "sentence": sentence, "hit": m.group(1)})
        if len(out) >= limit:
            break
    return out


def negated_tokens(text, tokens):
    """找出处于排除性语境里的特征词（如"这和月神无关"里的「月神」）。

    返回这些词构成的集合；调用方应把它们从该碎片的特征词里剔除，
    否则"和 X 无关"的注释会被当成"关于 X"的证据，把不相干的碎片并进来。
    """
    if not text or not tokens:
        return set()
    neg = set()
    for m in NEGATION_CUE_RE.finditer(text):
        window = text[max(0, m.start() - NEGATION_WINDOW):m.start()]
        for run in CJK_RUN_RE.findall(window):
            n = len(run)
            for size in range(MIN_NGRAM, MAX_NGRAM + 1):
                if n < size:
                    continue
                for i in range(n - size + 1):
                    g = run[i:i + size]
                    if g in tokens:
                        neg.add(g)
    return neg


def _valid_date(y, m, d):
    try:
        y, m, d = int(y), int(m), int(d)
    except (TypeError, ValueError):
        return False
    return 1900 <= y <= 2200 and 1 <= m <= 12 and 1 <= d <= 31


def _fmt(y, m, d):
    return "%04d-%02d-%02d" % (int(y), int(m), int(d))


def _search_date(text, ref_year=None):
    """在一段文本里找日期。返回 (ts, partial) 或 (None, False)。"""
    for m in DATE_FULL_RE.finditer(text or ""):
        if _valid_date(*m.groups()):
            return _fmt(*m.groups()), False
    for m in DATE_MD_RE.finditer(text or ""):
        mm, dd = m.groups()
        if _valid_date(ref_year or 2000, mm, dd):
            return _fmt(ref_year or 2000, mm, dd), True
    return None, False


def extract_timestamp(name, content, mtime):
    """按可信度递降解时间戳。返回 (ts, source, partial)。

    1) 文件名任意位置（自由命名 → 不做锚定，全文搜）
    1b) 文件名整体是纯数字："0312"（月日）/ "20240305"（年月日）
    2) 正文前 CONTENT_TS_HEAD_LINES 行
    3) 文件 mtime（可信度低，source="mtime" 供 GUI 淡显提示）
    """
    ref_year = datetime.fromtimestamp(mtime).year if mtime else datetime.now().year

    ts, partial = _search_date(name, ref_year)
    if ts:
        return ts, "filename", partial

    bare = (name or "").strip()
    m = BARE_YMD_RE.match(bare)
    if m and _valid_date(*m.groups()):
        return _fmt(*m.groups()), "filename", False
    m = BARE_MDD_RE.match(bare)
    if m and _valid_date(ref_year, m.group(1), m.group(2)):
        return _fmt(ref_year, m.group(1), m.group(2)), "filename", True

    head = "\n".join([ln for ln in (content or "").splitlines() if ln.strip()][:CONTENT_TS_HEAD_LINES])
    ts, partial = _search_date(head, ref_year)
    if ts:
        return ts, "content", partial

    if mtime:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"), "mtime", False
    return None, "unknown", False


def topic_hint(stem):
    """从自由命名里抽弱提示：去掉日期后的最长语义片段（可能为空）。

    仅用于两处：① 单例簇的命名；② 与内容特征词**共同**命中时作为佐证。
    单独命中不足以成簇（见 HINT_ONLY_SCORE）。
    """
    s = DATE_FULL_RE.sub(" ", stem or "")
    s = DATE_MD_RE.sub(" ", s)
    parts = [p.strip() for p in HINT_SEP_RE.split(s) if p.strip()]
    cands = [p for p in parts
             if len(p) >= 2 and not p.isdigit() and p not in STOP_TOKENS
             and not re.fullmatch(r"[0-9_\-]+", p)]
    if not cands:
        return ""
    return max(cands, key=lambda p: (len(p), p))


def iter_scrap_files(dir_):
    """列出碎片文件（跳过 _backup/ 等下划线目录与隐藏文件）。"""
    root = Path(dir_)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if any(part.startswith(("_", ".")) for part in p.relative_to(root).parts):
            continue
        if p.is_file() and p.suffix.lower() in SCRAP_EXTS:
            out.append(p)
    return out


def load_scrap(path):
    """读单个碎片；编码失败返回 None（调用方给出警告）。"""
    p = Path(path)
    try:
        text = read_text(p)
    except Exception:
        return None
    st = p.stat()
    ts, ts_source, ts_partial = extract_timestamp(p.stem, text, st.st_mtime)
    norm = normalize_text(text)
    toks = extract_tokens(norm)
    neg = negated_tokens(text, toks)
    for t in neg:
        toks.pop(t, None)
    return Scrap(
        path=str(p),
        name=p.name,
        stem=p.stem,
        chars=len(re.sub(r"\s+", "", text)),
        lines=len(text.splitlines()),
        ts=ts,
        ts_source=ts_source,
        ts_partial=ts_partial,
        topic_hint=topic_hint(p.stem),
        lookahead=extract_lookaheads(text),
        negated=sorted(neg),
        excerpt=re.sub(r"\s+", " ", text.strip())[:200],
        norm_text=norm,
        tokens=toks,
    )


# --------------------------------------------------------------------------
# 聚类
# --------------------------------------------------------------------------

def _select_keywords(scored, counts, k):
    """挑选特征词，并做"极大 n-gram"归并。

    没有分词器时，一条短语会派生出大量重叠 n-gram（「月兔密室」→ 兔密室/月兔密/兔密/密室），
    它们几乎同分，会把关键词名额占满，把真正的短特征词（「月兔」「月神」）挤出去 →
    该合并的簇合不上。判据：若 t 被某个更长词 T 包含，且 **count(T) >= count(t)**
    （即 t 的每次出现都落在 T 里），则 t 只是 T 的碎片，丢弃；
    反之（如「月兔」出现 2 次、「月兔密室」只出现 1 次）t 有独立出现，保留。
    """
    out = []
    for t, sc_ in scored:
        if len(out) >= k:
            break
        if any(t in kept and counts.get(kept, 0) >= counts.get(t, 0) for kept, _ in out):
            continue
        # t 更长且出现次数不少于已选词 → 已选词只是它的碎片，替换掉
        out = [(kept, s2) for kept, s2 in out
               if not (kept in t and counts.get(t, 0) >= counts.get(kept, 0))]
        out.append((t, sc_))
        if len(out) >= k:
            break
    return out


def score_keywords(scraps, per_scrap=KEYWORDS_PER_SCRAP):
    """给每个碎片挑特征词（长度加权 + 文档频率惩罚 + 极大 n-gram 归并）。"""
    docs = []
    for s in scraps:
        toks = s.tokens if s.tokens else extract_tokens(s.norm_text)
        docs.append(dict(toks))
    df = Counter()
    for d in docs:
        for t in d:
            df[t] += 1
    total = len(scraps)
    for s, d in zip(scraps, docs):
        scored = []
        for t, c in d.items():
            if not is_discriminative(t, df[t], total):
                continue
            len_w = 1.6 if len(t) >= 3 else 1.0
            score = len_w * (1.0 + min(c, 5) * 0.1) / (df[t] ** 0.5)
            scored.append((t, round(score, 4), df[t]))
        # 两段排序：先"被 ≥2 个碎片共享"的词（才可能驱动聚类），再按分数。
        # 否则跨词边界的垃圾 n-gram（如"个人沉默"）凭长度压过"月神"这类真特征词，
        # 把关键词名额占满 → 该合并的簇合不上。
        scored.sort(key=lambda x: (-(x[2] >= 2), -x[1], -len(x[0]), x[0]))
        # 匹配用候选集 = 全部有区分度的词（不截断、不做 n-gram 归并）。
        # 展示用 keywords 才需要"干净"（做极大 n-gram 归并、只留前 6 个）；
        # 若拿 keywords 去判定成簇，会漏掉"月兔"这类被归并掉的短词 → 该合并的簇合不上。
        s.candidates = {t for t, _sc, _d in scored}
        s.keywords = _select_keywords([(t, sc_) for t, sc_, _ in scored], d, per_scrap)
    return df


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
            return True
        return False


def _top_terms(tokens, k=4):
    """挑出"极大"词用于展示，避免证据里出现 兔密室/月兔密/兔密 这类重叠碎片。"""
    out = []
    for t in sorted(tokens, key=lambda x: (-len(x), x)):
        if any(t in kept for kept in out):
            continue
        out.append(t)
        if len(out) >= k:
            break
    return out


def build_edges(scraps):
    """两两比较，返回 [(i, j, reasons)]。规则可解释、可审计。

    成簇判据（满足其一）：
    1) 共享特征词 ≥2 个；
    2) 共享 1 个长特征词（≥3 字，如「月兔密室」）；
    3) 共享 1 个只在这两个碎片里出现的特征词（kdf ≤ 2，如短篇里的人物名「月兔」）。
    第 3 条是为"自由命名 + 短笔记"场景补的：碎片很短，同一实体的称呼往往只出现一次，
    若坚持 ≥2 个共享词，写同一件事的两条笔记就永远聚不到一起。
    """
    kdf = Counter(t for s in scraps for t in (s.candidates or {t for t, _ in s.keywords}))
    edges = []
    n = len(scraps)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = scraps[i], scraps[j]
            reasons = []

            set_a = a.candidates or {t for t, _ in a.keywords}
            set_b = b.candidates or {t for t, _ in b.keywords}
            shared = sorted(set_a & set_b, key=lambda t: (-len(t), t))
            long_shared = [t for t in shared if len(t) >= SHARED_TOKEN_MIN_LEN]
            rare_shared = [t for t in shared if kdf[t] <= 2]

            if len(shared) >= MIN_SHARED_TOKENS:
                reasons.append("共享特征词 %d 个：%s"
                               % (len(shared), "、".join(_top_terms(shared))))
            elif long_shared:
                reasons.append("共享长特征词：「%s」" % long_shared[0])
            elif rare_shared:
                reasons.append("共享专属特征词：「%s」（仅这 2 个碎片有）" % rare_shared[0])

            if a.topic_hint and a.topic_hint == b.topic_hint:
                if reasons or HINT_ONLY_SCORE:
                    reasons.append("命名提示一致：「%s」" % a.topic_hint)

            ratio = difflib.SequenceMatcher(None, a.norm_text, b.norm_text).ratio()
            if ratio >= SIM_THRESHOLD:
                reasons.append("文本相似度 %.2f" % ratio)

            if reasons:
                edges.append((i, j, reasons))
    return edges


def _cluster_name(members):
    """用簇内共享度最高的特征词命名，退化时用命名提示。"""
    counter = Counter()
    for s in members:
        for t, _ in s.keywords:
            counter[t] += 1
    if counter:
        shared = [(t, c) for t, c in counter.items() if c >= 2]
        pool = shared or list(counter.items())
        pool.sort(key=lambda x: (-x[1], -len(x[0]), x[0]))
        best = pool[0][0]
        if len(pool) > 1 and not shared:
            return best
        return best
    hints = [s.topic_hint for s in members if s.topic_hint]
    if hints:
        return Counter(hints).most_common(1)[0][0]
    return members[0].stem


def cluster_scraps(scraps):
    """聚类：并查集合并 + 命名 + 排序。返回 list[Cluster]。"""
    if not scraps:
        return []
    uf = _UnionFind(len(scraps))
    reasons_by_root = defaultdict(list)
    for i, j, reasons in build_edges(scraps):
        if uf.union(i, j):
            reasons_by_root[i].append("与「%s」：%s" % (scraps[j].name, "；".join(reasons)))

    groups = defaultdict(list)
    for idx in range(len(scraps)):
        groups[uf.find(idx)].append(scraps[idx])

    clusters = []
    used_names = Counter()
    for root, members in groups.items():
        members.sort(key=lambda s: (s.ts or "9999-99-99", s.stem))
        name = _cluster_name(members)
        used_names[name] += 1
        if used_names[name] > 1:
            name = "%s (%d)" % (name, used_names[name])
        cluster = Cluster(name=name,
                          kind="merged" if len(members) > 1 else "single",
                          members=members,
                          keywords=[t for t, _ in Counter(
                              t for s in members for t, _ in s.keywords).most_common(CLUSTER_KEYWORDS)],
                          evidence=reasons_by_root.get(root, []))
        if cluster.kind == "single" and not cluster.evidence:
            cluster.evidence = ["独立碎片：未与其他碎片共享特征词"]
        clusters.append(cluster)

    clusters.sort(key=lambda c: (c.members[0].ts or "9999-99-99", c.name))
    return clusters


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------

def content_fingerprint(scraps):
    """碎片内容指纹（只含文件名与正文，不含时间/mtime 等易变字段）。

    stage1 用它判断"碎片是否有实质变化"——若把 generated_at 之类也算进去，
    每次运行都会误判为有变化，导致设定集被反复重归并（烧钱）。
    """
    h = hashlib.sha256()
    for p in sorted(Path(s.path) for s in scraps):
        h.update(p.name.encode("utf-8"))
        try:
            h.update(read_text(p).encode("utf-8", errors="replace"))
        except Exception:
            h.update(b"<unreadable>")
        h.update(b"\x00")
    return "sha256:" + h.hexdigest()


def build_inline_batches(clusters, budget=None):
    """按上限切分内联批次，避免 llm_client 的 220000 字符上限静默丢文件。

    按**碎片**粒度装箱（而非按簇），因为单个簇就可能超过上限；同一簇允许跨批。
    budget 默认取模块常量（运行时读取，便于测试与后续改成可配置）。
    """
    budget = budget or INLINE_CHAR_BUDGET
    units = [(c.name, s.path, s.chars) for c in clusters for s in c.members]
    batches, cur, cur_chars = [], [], 0
    for name, path, chars in units:
        if cur and cur_chars + chars > budget:
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append((name, path, chars))
        cur_chars += chars
        if chars > budget:     # 单个碎片就超限：独占一批，需人工介入
            batches.append(cur)
            cur, cur_chars = [], 0
    if cur:
        batches.append(cur)
    out = []
    for i, batch in enumerate(batches):
        names = []
        for name, _p, _c in batch:
            if name not in names:
                names.append(name)
        out.append({
            "index": i + 1,
            "chars": sum(c for _n, _p, c in batch),
            "clusters": names,
            "files": [p for _n, p, _c in batch],
        })
    return out


def build_index(scraps, clusters, dir_=DEFAULT_SCRAP_DIR):
    """组装索引 dict（可直接 json.dumps）。"""
    total_chars = sum(s.chars for s in scraps)
    merged = [c for c in clusters if c.kind == "merged"]
    mtime_ts = [s.name for s in scraps if s.ts_source == "mtime"]
    lookaheads = [dict(la, file=s.name,
                       cluster=next((c.name for c in clusters if s in c.members), ""))
                  for s in scraps for la in s.lookahead]

    warnings = []
    if mtime_ts:
        warnings.append("%d 个碎片未在文件名/正文中找到日期，已回退文件修改时间（不可信）：%s"
                        % (len(mtime_ts), "、".join(mtime_ts[:5])))
    if total_chars > INLINE_CHAR_BUDGET:
        warnings.append("碎片总字符 %d 超过单批内联上限 %d，stage1 将按 inline_batches 分批处理"
                        % (total_chars, INLINE_CHAR_BUDGET))

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dir": str(dir_),
        "content_fingerprint": content_fingerprint(scraps),
        "stats": {
            "scrap_count": len(scraps),
            "cluster_count": len(clusters),
            "merged_cluster_count": len(merged),
            "singleton_count": len(clusters) - len(merged),
            "total_chars": total_chars,
            "lookahead_count": len(lookaheads),
            "ts_from_filename": sum(1 for s in scraps if s.ts_source == "filename"),
            "ts_from_content": sum(1 for s in scraps if s.ts_source == "content"),
            "ts_from_mtime": len(mtime_ts),
        },
        "warnings": warnings,
        "clusters": [c.to_json() for c in clusters],
        "timeline": [{"name": s.name, "ts": s.ts, "ts_source": s.ts_source,
                      "ts_partial": s.ts_partial,
                      "cluster": next((c.name for c in clusters if s in c.members), "")}
                     for s in sorted(scraps, key=lambda x: (x.ts or "9999-99-99", x.stem))],
        "lookaheads": lookaheads,
        "inline_batches": build_inline_batches(clusters),
    }


def scan_scraps(dir_=DEFAULT_SCRAP_DIR):
    """扫描目录。返回 (scraps, warnings)。目录不存在时自动创建（空结果）。"""
    root = Path(dir_)
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        return [], ["碎片目录不存在，已自动创建：%s" % root]
    scraps, warnings = [], []
    for p in iter_scrap_files(root):
        s = load_scrap(p)
        if s is None:
            warnings.append("读取失败（编码/权限），已跳过：%s" % p.name)
            continue
        scraps.append(s)
    return scraps, warnings


def write_index(index, out=DEFAULT_INDEX_OUT):
    write_text(out, json.dumps(index, ensure_ascii=False, indent=2))
    return out


def print_summary(index, explain=False):
    st = index["stats"]
    print("[scrap_cluster] 目录 %s" % index["dir"])
    print("  碎片 %d 个 / 簇 %d 个（合并簇 %d，独立碎片 %d）/ 总 %d 字"
          % (st["scrap_count"], st["cluster_count"], st["merged_cluster_count"],
             st["singleton_count"], st["total_chars"]))
    print("  时间戳来源：文件名 %d / 正文 %d / 文件时间 %d"
          % (st["ts_from_filename"], st["ts_from_content"], st["ts_from_mtime"]))
    for c in index["clusters"]:
        tag = "合并" if c["kind"] == "merged" else "独立"
        rng = c["ts_range"] or ["?"]
        print("  ── [%s] %s（%d 个，%s）" % (tag, c["name"], c["count"], rng[0]))
        for s in c["scraps"]:
            la = "  前瞻%d" % len(s["lookahead"]) if s["lookahead"] else ""
            print("       - %s  [%s/%s]%s" % (s["name"], s["ts"] or "无日期", s["ts_source"], la))
        if explain:
            for e in c["evidence"]:
                print("       证据：%s" % e)
    if index["lookaheads"]:
        print("  前瞻备忘 %d 条（归并时需重点消化）" % st["lookahead_count"])
    for w in index["warnings"]:
        print("  [WARN] %s" % w)
    if len(index["inline_batches"]) > 1:
        print("  内联分批 %d 批（超单批上限）" % len(index["inline_batches"]))


def collect(dir_=DEFAULT_SCRAP_DIR):
    """一步到位：扫描 → 关键词 → 聚类 → 索引。返回 (index, warnings)。"""
    scraps, warnings = scan_scraps(dir_)
    score_keywords(scraps)
    clusters = cluster_scraps(scraps)
    index = build_index(scraps, clusters, dir_=dir_)
    index["warnings"] = warnings + index["warnings"]
    return index, warnings


def main():
    parser = argparse.ArgumentParser(description="NovelForge 原始碎片聚类（确定性）")
    parser.add_argument("--dir", default=DEFAULT_SCRAP_DIR, help="碎片目录")
    parser.add_argument("--out", default=DEFAULT_INDEX_OUT, help="索引输出路径")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--explain", action="store_true", help="打印每簇合并证据")
    parser.add_argument("--no-write", action="store_true", help="只分析不写文件")
    args = parser.parse_args()

    index, warnings = collect(args.dir)
    if not index["stats"]["scrap_count"]:
        print("[scrap_cluster] %s 下没有碎片文件（.md/.markdown/.txt）" % args.dir)
        for w in warnings:
            print("  [WARN] %s" % w)
        return 1 if "读取失败" in "".join(warnings) else 0

    if args.json:
        print(json.dumps(index, ensure_ascii=False, indent=2))
    else:
        print_summary(index, explain=args.explain)
    if not args.no_write:
        out = write_index(index, args.out)
        if not args.json:
            print("  索引 → %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
