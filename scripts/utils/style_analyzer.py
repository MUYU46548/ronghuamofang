# -*- coding: utf-8 -*-
"""文风分析与风格指令生成。

从参考文本提取风格特征，生成润色/写作时的风格指令。
特征维度：句式长度、修辞偏好、节奏模式、高频表达。
"""
import re
from collections import Counter


def extract_style_features(text):
    """从参考文本提取风格特征字典。"""
    if not text or len(text) < 100:
        return {}
    
    # 1. 句式长度分布（按句号/问号/感叹号切分）
    sentences = re.split(r'[。！？；\n]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sent_lengths = [len(s) for s in sentences]
    
    avg_len = sum(sent_lengths) / len(sent_lengths) if sent_lengths else 0
    short_ratio = sum(1 for l in sent_lengths if l < 15) / len(sent_lengths) if sent_lengths else 0
    long_ratio = sum(1 for l in sent_lengths if l > 40) / len(sent_lengths) if sent_lengths else 0
    
    # 2. 高频表达（2-4字词组，出现3次以上）
    words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    word_counts = Counter(words)
    common_phrases = [w for w, c in word_counts.most_common(20) if c >= 3]
    
    # 3. 修辞特征：比喻/拟人标记词
    metaphor_markers = ['像', '仿佛', '如同', '好似', '宛如', '犹如']
    personification_markers = ['低语', '沉默', '诉说', '倾听', '呼吸', '微笑', '叹息']
    metaphor_count = sum(text.count(m) for m in metaphor_markers)
    personification_count = sum(text.count(m) for m in personification_markers)
    
    # 4. 对话模式（引号内内容占比）
    dialogue = re.findall(r'["""]([^"""]+)["""]', text)
    dialogue_chars = sum(len(d) for d in dialogue)
    dialogue_ratio = dialogue_chars / len(text) if text else 0
    
    # 5. 段落节奏（平均段长）
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    avg_para_len = sum(len(p) for p in paragraphs) / len(paragraphs) if paragraphs else 0
    
    return {
        "avg_sentence_len": round(avg_len, 1),
        "short_sentence_ratio": round(short_ratio, 2),
        "long_sentence_ratio": round(long_ratio, 2),
        "common_phrases": common_phrases[:10],
        "metaphor_density": round(metaphor_count / len(sentences), 3) if sentences else 0,
        "personification_density": round(personification_count / len(sentences), 3) if sentences else 0,
        "dialogue_ratio": round(dialogue_ratio, 2),
        "avg_paragraph_len": round(avg_para_len, 1),
    }


def build_style_instruction(features):
    """根据特征生成风格指令文本，注入 prompt。"""
    if not features:
        return ""
    
    lines = []
    lines.append("\n## 风格模仿要求")
    lines.append("请严格模仿以下文风特征进行创作/润色：\n")
    
    # 句式长度
    avg = features.get("avg_sentence_len", 0)
    if avg > 0:
        lines.append(f"- **句式长度**：平均 {avg} 字/句")
        if features.get("short_sentence_ratio", 0) > 0.4:
            lines.append(f"  - 短句占比高（{features['short_sentence_ratio']*100:.0f}%），请保留简洁明快的节奏")
        if features.get("long_sentence_ratio", 0) > 0.3:
            lines.append(f"  - 长句占比高（{features['long_sentence_ratio']*100:.0f}%），请保留铺陈渲染的风格")
    
    # 修辞偏好
    meta = features.get("metaphor_density", 0)
    pers = features.get("personification_density", 0)
    if meta > 0.1:
        lines.append(f"- **修辞**：高频使用比喻（密度 {meta}），请保持具象化的意象表达")
    if pers > 0.05:
        lines.append(f"- **修辞**：高频使用拟人（密度 {pers}），请保持万物有灵的视角")
    
    # 对话模式
    dialogue = features.get("dialogue_ratio", 0)
    if dialogue > 0.3:
        lines.append(f"- **对话**：对话占比 {dialogue*100:.0f}%，请以对话推动叙事")
    elif dialogue < 0.1:
        lines.append(f"- **对话**：对话占比低（{dialogue*100:.0f}%），请保持内敛克制的叙事")
    
    # 段落节奏
    para_len = features.get("avg_paragraph_len", 0)
    if para_len > 0:
        lines.append(f"- **段落**：平均段长 {para_len} 字")
        if para_len < 100:
            lines.append("  - 段落短小精悍，节奏明快")
        elif para_len > 200:
            lines.append("  - 段落铺陈舒展，节奏沉稳")
    
    # 高频表达
    phrases = features.get("common_phrases", [])
    if phrases:
        lines.append(f"- **标志性表达**：{ '、'.join(phrases[:8]) }")
    
    lines.append("\n请在保持情节和人物关系不变的前提下，严格遵循以上风格特征。")
    
    return "\n".join(lines)


def load_style_reference(path):
    """从文件加载参考文本并返回风格指令。"""
    from utils.file_io import read_text
    try:
        text = read_text(path)
    except Exception:
        return ""
    features = extract_style_features(text)
    return build_style_instruction(features)
