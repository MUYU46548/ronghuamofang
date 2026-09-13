// 角色关系图的**纯数据装配**（无 DOM、无 d3 —— 便于用 Node 直接断言）
//
// 为什么单独一层：relations 是自由文本数组，解析/别名匹配/幽灵节点兜底全是确定性逻辑，
// 放在 .vue 里就只能靠 headless 浏览器验证。抽出来后 `Temp/test_role_graph.js` 可直接断言。
//
// 输入：setting.json（characters[].relations）+ appearances.json（出场次数/分级）+ 可选 alias
// 输出：{ nodes, links, unmatched, candidates, stats }

import { classifyRelation } from "./relationTypes.js";

/** 节点分级 → 描边样式（浅色主题硬编码，不依赖未实现的 c-* 类）。 */
export const LEVEL_STYLE = {
  major: { label: "主要", stroke: "#6b7180", width: 2.4, dash: null },
  minor: { label: "次要", stroke: "#8b91a0", width: 1.7, dash: null },
  background: { label: "背景/提及", stroke: "#c3c6cf", width: 1.4, dash: null },
  absent: { label: "未出场（写了没用）", stroke: "#c3c6cf", width: 1.4, dash: "3 3" },
  ghost: { label: "未入册（幽灵节点）", stroke: "#cfd2da", width: 1.2, dash: "2 3" },
};

export function levelStyle(level) {
  return LEVEL_STYLE[level] || LEVEL_STYLE.background;
}

/** 拆 "对端名: 描述"。无冒号时整体当对端名，描述为空。 */
export function splitRelation(s) {
  const raw = String(s == null ? "" : s).trim();
  if (!raw) return null;
  const i = raw.search(/[:：]/);
  if (i < 0) return { left: raw, detail: "" };
  return { left: raw.slice(0, i).trim(), detail: raw.slice(i + 1).trim() };
}

/** 对端名的候选写法：原文 → 去括号 → 括号内别名 → 斜杠/顿号拆分。 */
export function nameCandidates(left) {
  const out = [];
  const push = (x) => {
    const v = String(x == null ? "" : x).trim();
    if (v && !out.includes(v)) out.push(v);
  };
  push(left);
  push(String(left).replace(/[（(].*?[)）]/g, "").trim());
  for (const m of String(left).matchAll(/[（(](.*?)[)）]/g)) push(m[1]);
  for (const base of [...out]) {
    if (/[/、]/.test(base)) base.split(/[/、]/).forEach(push);
  }
  return out;
}

/**
 * 装配图数据。
 * @param {object} setting      setting.json（读 characters[].relations）
 * @param {object|null} appearances  appearances.json（可为 null → 均一节点大小）
 * @param {object} alias        { 别名: 正式名 } 可选，来自 data/setting/alias.json
 */
export function buildGraph(setting, appearances, alias) {
  const chars = Array.isArray(setting?.characters) ? setting.characters : [];
  const apChars = (appearances && appearances.characters) || {};
  const aliasMap = alias && typeof alias === "object" ? alias : {};

  const nodes = [];
  const byName = new Map();
  const byId = new Map();
  const rawByName = new Map();

  for (const c of chars) {
    if (!c || !c.name || byName.has(c.name)) continue;
    const ap = apChars[c.name] || (c.id ? apChars[c.id] : null) || null;
    const node = {
      id: c.id || c.name,
      key: c.name,
      name: c.name,
      role: c.role || "",
      traits: c.traits || "",
      appearance: c.appearance || "",
      experience: c.experience || "",
      source: c.source || "",
      locked: !!c.locked,
      ghost: false,
      total: ap ? Number(ap.total || 0) : 0,
      level: ap ? (ap.level || "absent") : "absent",
      chapters: ap ? Number(ap.chapters || 0) : 0,
      first: ap ? ap.first : null,
      degree: 0,
    };
    nodes.push(node);
    byName.set(node.name, node);
    if (node.id) byId.set(String(node.id), node);
    rawByName.set(node.name, c);
  }

  const ghostByName = new Map();
  function ghostFor(name) {
    if (ghostByName.has(name)) return ghostByName.get(name);
    const g = {
      id: "ghost::" + name,
      key: name,
      name,
      role: "",
      traits: "",
      appearance: "",
      experience: "",
      source: "",
      locked: false,
      ghost: true,
      total: 0,
      level: "ghost",
      chapters: 0,
      first: null,
      degree: 0,
    };
    ghostByName.set(name, g);
    nodes.push(g);
    return g;
  }

  function resolve(left) {
    const expanded = [];
    for (const c of nameCandidates(left)) {
      expanded.push(c);
      const a = aliasMap[c];
      if (a) expanded.push(a);
    }
    for (const c of expanded) {
      if (byName.has(c)) return byName.get(c);
      if (byId.has(c)) return byId.get(c);
    }
    return null;
  }

  const linkMap = new Map();
  const unmatched = [];

  for (const [name, c] of rawByName) {
    const rels = Array.isArray(c.relations) ? c.relations : [];
    for (const r of rels) {
      const sp = splitRelation(r);
      if (!sp || !sp.left) continue;
      const hit = resolve(sp.left);
      let target;
      if (hit) {
        target = hit;
      } else {
        target = ghostFor(sp.left);
        unmatched.push({ from: name, name: sp.left, detail: sp.detail });
      }
      if (target.key === name) continue;              // 自环跳过
      const [a, b] = name < target.key ? [name, target.key] : [target.key, name];
      const k = a + "\u0000" + b;
      let lk = linkMap.get(k);
      if (!lk) {
        lk = { key: k, source: a, target: b, entries: [], type: "other", ghost: false };
        linkMap.set(k, lk);
      }
      lk.entries.push({ from: name, text: sp.detail || sp.left, raw: String(r) });
      const t = classifyRelation(r);
      if (lk.type === "other" && t !== "other") lk.type = t;   // 取最具体的类型
      if (target.ghost) lk.ghost = true;
    }
  }

  const links = [...linkMap.values()];
  const deg = new Map();
  for (const lk of links) {
    deg.set(lk.source, (deg.get(lk.source) || 0) + 1);
    deg.set(lk.target, (deg.get(lk.target) || 0) + 1);
  }
  for (const n of nodes) n.degree = deg.get(n.key) || 0;

  const candidates = (appearances && appearances.new_candidates) || [];
  return {
    nodes,
    links,
    unmatched,
    candidates,
    stats: {
      nodeCount: nodes.length,
      realCount: nodes.length - ghostByName.size,
      ghostCount: ghostByName.size,
      linkCount: links.length,
      unmatchedCount: unmatched.length,
      maxTotal: nodes.reduce((m, n) => Math.max(m, n.total), 0),
      maxDegree: nodes.reduce((m, n) => Math.max(m, n.degree), 0),
    },
  };
}

/** 一阶邻居集合（含自身），用于点击高亮。 */
export function neighborsOf(node, links) {
  const set = new Set([node.key]);
  for (const lk of links) {
    const s = typeof lk.source === "object" ? lk.source.key : lk.source;
    const t = typeof lk.target === "object" ? lk.target.key : lk.target;
    if (s === node.key) set.add(t);
    if (t === node.key) set.add(s);
  }
  return set;
}
