// 角色关系类型归类表（独立文件，便于调优 —— 改这里不影响渲染逻辑）
//
// 数据来源：setting.json 的 characters[].relations，是**字符串数组**且形如
//   "夏婉芸: 侍从/徒弟/继承者/眷属/实验对象，月神宠爱万分…"
// 左半是「对端名称」，右半是自由描述。类型只能从**描述文本**关键词启发式归类。
//
// 匹配顺序即优先级：越具体的类型放前面（如"实验体"要先于"同僚"，
// 否则"眷属/实验对象"会被"伙伴"误判成同僚）。

export const RELATION_TYPES = [
  {
    key: "created",
    label: "实验体/创造物",
    color: "#7fae9f",
    keywords: ["实验体", "实验对象", "创造物", "造物", "造出", "制造", "复制",
               "样本", "产物", "眷属", "版本实验", "研制"],
  },
  {
    key: "kin",
    label: "亲属",
    color: "#c98fa8",
    keywords: ["父亲", "母亲", "父母", "兄长", "弟弟", "姐姐", "妹妹", "兄弟", "姐妹",
               "儿子", "女儿", "丈夫", "妻子", "夫妻", "家族", "血亲", "血脉", "亲族",
               "叔", "舅", "姨", "祖母", "祖父", "孙"],
  },
  {
    key: "master",
    label: "师徒/侍从",
    color: "#9b8fc4",
    keywords: ["师父", "师傅", "师尊", "徒弟", "弟子", "学生", "侍从", "仆", "属下",
               "部下", "继承者", "继承", "导师", "教导", "授业", "供差遣"],
  },
  {
    key: "love",
    label: "恋慕/伴侣",
    color: "#d0889b",
    keywords: ["恋慕", "爱慕", "恋人", "伴侣", "心上人", "暗恋", "婚", "钟情", "宠爱"],
  },
  {
    key: "hostile",
    label: "敌对",
    color: "#c47f7f",
    keywords: ["敌对", "宿敌", "仇", "对手", "對手", "追杀", "背叛", "对立", "冲突",
               "诅咒", "戒备", "误解", "创伤", "戒备心"],
  },
  {
    key: "ally",
    label: "同僚/组织",
    color: "#7f9fc4",
    keywords: ["同僚", "同事", "盟友", "同伴", "伙伴", "成员", "组织", "委员会", "机关",
               "部队", "军团", "军", "阵营", "团队", "小组", "文明", "奠基", "营救", "同乡"],
  },
  {
    key: "other",
    label: "其他",
    color: "#a8adba",
    keywords: [],   // 兜底：无任何关键词命中
  },
];

const TYPE_BY_KEY = Object.fromEntries(RELATION_TYPES.map((t) => [t.key, t]));

/** 按 key 取类型定义（未知 key 回退「其他」）。 */
export function relationTypeOf(key) {
  return TYPE_BY_KEY[key] || TYPE_BY_KEY.other;
}

/**
 * 从关系描述文本归类。传入整条关系字符串（左名 + 右描述）即可 ——
 * 左侧的组织名（如「术战部队」）也算有效线索。
 */
export function classifyRelation(text) {
  const s = String(text || "");
  if (!s) return "other";
  for (const t of RELATION_TYPES) {
    if (!t.keywords.length) continue;
    for (const kw of t.keywords) {
      if (s.includes(kw)) return t.key;
    }
  }
  return "other";
}
