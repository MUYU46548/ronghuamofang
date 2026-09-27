# -*- coding: utf-8 -*-
"""风格偏差检测（Style Drift Detection）。

从范文提取风格基准，对比重写后的段落，输出偏差评分。
参考竞品扫描：AI-Novel 写法引擎特征池、Velith 风格锁定。

特征维度：
1. 句长分布（平均每句字符数）
2. 词汇丰富度（不重复词 / 总词数）
3. 标点密度（每百字标点符号数）
4. 对话比例（含引号的文本占比）
5. 特定修辞频率（如比喻词：如、像、仿佛）
"""
import re
import statistics
from pathlib import Path


def split_sentences(text):
    """按句末标点切分句子。"""
    sentences = re.split(r'[。！？!?\n]+', text)
    return [s.strip() for s in sentences if s.strip()]


def extract_style_features(text):
    """提取文风特征。"""
    if not text:
        return {}

    sentences = split_sentences(text)
    total_chars = len(text)
    if total_chars == 0:
        return {}

    # 句长
    sentence_lengths = [len(s) for s in sentences]
    avg_sentence_length = statistics.mean(sentence_lengths) if sentence_lengths else 0

    # 词汇丰富度（中文字去重率）
    chars = list(text.replace(" ", "").replace("\n", ""))
    unique_chars = len(set(chars)) if chars else 0
    vocab_richness = unique_chars / len(chars) if chars else 0

    # 标点密度（每百字）
    punct_count = len(re.findall(r'[，。、；：！？,.;:？！""''（）()—]', text))
    punct_density = (punct_count / total_chars) * 100 if total_chars > 0 else 0

    # 对话比例（含引号的文本占比）
    dialogue_chars = sum(len(m) for m in re.findall(r'["「『].*?["」』]', text))
    dialogue_ratio = dialogue_chars / total_chars if total_chars > 0 else 0

    # 修辞频率（每百字）
    metaphor_words = len(re.findall(r'(如|像|仿佛|好像|犹如|好似|宛若|如同|恰似|若)', text))
    metaphor_freq = (metaphor_words / total_chars) * 100 if total_chars > 0 else 0

    # 心理描写频率
    mental_words = len(re.findall(r'(想|觉得|感到|意识到|心里|内心|暗暗|默念|思|念|虑)', text))
    mental_freq = (mental_words / total_chars) * 100 if total_chars > 0 else 0

    # 形容词密度（的/地 字频率）
    adj_aux = len(re.findall(r'[的地]', text))
    adj_density = (adj_aux / total_chars) * 100 if total_chars > 0 else 0

    return {
        "total_chars": total_chars,
        "sentence_count": len(sentences),
        "avg_sentence_length": round(avg_sentence_length, 1),
        "vocab_richness": round(vocab_richness, 3),
        "punct_density_per_100": round(punct_density, 1),
        "dialogue_ratio": round(dialogue_ratio, 3),
        "metaphor_freq_per_100": round(metaphor_freq, 1),
        "mental_freq_per_100": round(mental_freq, 1),
        "adj_aux_per_100": round(adj_density, 1),
    }


def compute_drift(features_before, features_after, weights=None):
    """计算两个特征集之间的偏差。
    
    返回各维度偏差 (0~1) 和综合偏差评分。
    weights: 各维度权重，默认等权。
    """
    if weights is None:
        weights = {
            "avg_sentence_length": 1.0,
            "vocab_richness": 0.5,
            "punct_density_per_100": 1.0,
            "dialogue_ratio": 1.0,
            "metaphor_freq_per_100": 1.5,
            "mental_freq_per_100": 1.0,
            "adj_aux_per_100": 1.0,
        }

    if not features_before or not features_after:
        return {"error": "特征提取失败"}

    drifts = {}
    total_weight = 0
    weighted_drift_sum = 0

    for key, weight in weights.items():
        v1 = features_before.get(key, 0)
        v2 = features_after.get(key, 0)
        if v1 == 0 and v2 == 0:
            drift = 0.0
        elif v1 == 0 or v2 == 0:
            drift = 1.0
        else:
            drift = abs(v2 - v1) / max(abs(v1), abs(v2))
        drifts[key] = round(drift, 3)
        weighted_drift_sum += drift * weight
        total_weight += weight

    overall = round(weighted_drift_sum / total_weight, 3) if total_weight > 0 else 0

    # 标记高偏差维度（>0.3 视为显著变化）
    high_drift_dims = [k for k, v in drifts.items() if v > 0.3]

    return {
        "overall_drift": overall,
        "dimension_drifts": drifts,
        "high_drift_dimensions": high_drift_dims,
        "is_significant": overall > 0.25 or len(high_drift_dims) >= 2,
    }


def analyze_paragraph_drift(original_text, rewritten_text, context_before=None, context_after=None):
    """对比段落重写前后的风格偏差。
    
    如果提供 context_before/after（相邻段落），则对比上下文稳定性。
    """
    orig_features = extract_style_features(original_text)
    rewritten_features = extract_style_features(rewritten_text)

    # 目标段落本身的漂移
    para_drift = compute_drift(orig_features, rewritten_features)

    result = {
        "paragraph": {
            "features_before": orig_features,
            "features_after": rewritten_features,
            "drift": para_drift,
        }
    }

    # 上下文漂移（如果提供）
    if context_before and context_after:
        ctx_features_before = extract_style_features(context_before)
        ctx_features_after = extract_style_features(context_after)
        ctx_drift = compute_drift(ctx_features_before, ctx_features_after)
        result["context"] = {
            "features_before": ctx_features_before,
            "features_after": ctx_features_after,
            "drift": ctx_drift,
        }

    return result


def generate_style_baseline_from_file(file_path):
    """从范文文件提取风格基准。"""
    path = Path(file_path)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return extract_style_features(text)


def format_drift_report(drift_result):
    """格式化偏差报告为可读文本。"""
    lines = []
    para = drift_result.get("paragraph", {})
    features_b = para.get("features_before", {})
    features_a = para.get("features_after", {})
    drift = para.get("drift", {})

    lines.append("=== 风格偏差报告 ===")
    lines.append(f"综合偏差: {drift.get('overall_drift', '?'):.3f} {'⚠ 显著' if drift.get('is_significant') else '✓ 正常'}")
    lines.append("")
    lines.append(f"{'维度':<25} {'重写前':>10} {'重写后':>10} {'偏差':>8}")
    lines.append("-" * 55)
    for dim, val in drift.get("dimension_drifts", {}).items():
        b = features_b.get(dim, 0)
        a = features_a.get(dim, 0)
        marker = " ⚠" if val > 0.3 else ""
        lines.append(f"{dim:<25} {str(b):>10} {str(a):>10} {val:>8.3f}{marker}")

    if drift.get("high_drift_dimensions"):
        lines.append("")
        lines.append(f"高偏差维度: {', '.join(drift['high_drift_dimensions'])}")

    return "\n".join(lines)
